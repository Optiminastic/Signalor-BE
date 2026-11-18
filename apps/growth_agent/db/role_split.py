"""
Database role split (Phase 1): what ``growth_apply_role_split`` does and what
``growth_release_check`` verifies. Postgres only.

After ``deploy/sql/phase1_roles.sql`` has created the roles:

- every table, view and sequence in ``public`` is owned by ``signalor_owner``
  (migrations), so the runtime role ``signalor_app`` can't alter a table,
  disable or drop a trigger, or replace a trigger function;
- ``signalor_app`` gets SELECT, INSERT, UPDATE, DELETE on tables and
  USAGE, SELECT on sequences, never TRUNCATE, TRIGGER or REFERENCES;
- on the append-only tables it loses UPDATE (the audit log keeps UPDATE of
  ``task_id`` only, for a deleted task); on the safety-change table it can't
  INSERT either, so it can't forge or suppress a change record;
- the function that writes change rows, and the trigger functions that call it,
  run as ``signalor_owner`` (SECURITY DEFINER, pinned search_path), and the
  runtime role can't call the writer directly.

Moving every table (not only the monitored ones) keeps one rule: the runtime
role owns nothing and has no DDL. Re-run after every migration (``scripts/migrate.sh``
does), so tables added later get the same treatment.
"""

from __future__ import annotations

from ..services.db_target import MONITORED_TABLES, OWNER_ROLE, RUNTIME_ROLE

APPEND_ONLY_TABLES = (
    "growth_agent_actionversion",
    "growth_agent_approvaldecision",
    "growth_agent_actionexecutionevent",
    "growth_agent_dispatchattemptledger",
    "growth_agent_agentactionlog",
    "growth_agent_approvalbatch",
    "growth_agent_approvalbatchitem",
    "growth_agent_growthsafetychange",
    "growth_agent_growthsafetyreview",
    "growth_agent_safetyconfirmationevent",
)
AUDIT_LOG = "growth_agent_agentactionlog"
CHANGE_LOG = "growth_agent_growthsafetychange"
CHANGE_WRITER = "growth_safety_insert(bigint, text, bigint, text, text, text, text, boolean)"
DEFINER_FUNCTIONS = (
    CHANGE_WRITER,
    "growth_watch_organization()",
    "growth_watch_integration()",
    "growth_watch_github()",
)
OTHER_FUNCTIONS = ("growth_append_only()",)
PINNED_PATH = "pg_catalog, public"

_NOT_OWNED = """
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
      AND pg_get_userbyid(c.relowner) <> %s
      AND NOT (c.relkind = 'S' AND EXISTS (
          SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
    ORDER BY c.relname
"""
_KIND = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}


def roles_exist(cursor) -> bool:
    cursor.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)", [OWNER_ROLE, RUNTIME_ROLE])
    return cursor.fetchone()[0] == 2


def _existing_functions(cursor) -> set[str]:
    cursor.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'growth\\_%%'"
    )
    return {row[0] for row in cursor.fetchall()}


def _existing_tables(cursor, names) -> set[str]:
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)", [list(names)]
    )
    return {row[0] for row in cursor.fetchall()}


def statements(cursor) -> list[str]:
    """The SQL that brings this database to the split, given its current state."""
    sql = []
    cursor.execute(_NOT_OWNED, [OWNER_ROLE])
    for name, kind in cursor.fetchall():
        sql.append(f'ALTER {_KIND[kind]} public."{name}" OWNER TO {OWNER_ROLE}')
    sql += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}",
        f"REVOKE TRUNCATE, TRIGGER, REFERENCES ON ALL TABLES IN SCHEMA public FROM {RUNTIME_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}",
        f"REVOKE CREATE ON SCHEMA public FROM {RUNTIME_ROLE}, PUBLIC",
    ]
    for table in sorted(_existing_tables(cursor, APPEND_ONLY_TABLES)):
        sql.append(f"REVOKE UPDATE ON public.{table} FROM {RUNTIME_ROLE}")
        if table == AUDIT_LOG:
            sql.append(f"GRANT UPDATE (task_id) ON public.{table} TO {RUNTIME_ROLE}")
        if table == CHANGE_LOG:
            sql.append(f"REVOKE INSERT ON public.{table} FROM {RUNTIME_ROLE}")
    functions = _existing_functions(cursor)
    for function in DEFINER_FUNCTIONS + OTHER_FUNCTIONS:
        if function not in functions:
            continue
        sql.append(f"ALTER FUNCTION public.{function} OWNER TO {OWNER_ROLE}")
        if function in DEFINER_FUNCTIONS:
            sql.append(f"ALTER FUNCTION public.{function} SECURITY DEFINER SET search_path = {PINNED_PATH}")
    if CHANGE_WRITER in functions:
        sql.append(f"REVOKE EXECUTE ON FUNCTION public.{CHANGE_WRITER} FROM PUBLIC, {RUNTIME_ROLE}")
    return sql


def apply(cursor) -> list[str]:
    """Run the split in the caller's transaction. Returns the statements run."""
    sql = statements(cursor)
    for statement in sql:
        cursor.execute(statement)
    return sql


def problems(cursor) -> list[str]:
    """Why this database is not split as it should be (empty when it is)."""
    if not roles_exist(cursor):
        return [f"Roles {OWNER_ROLE} and {RUNTIME_ROLE} don't exist (run deploy/sql/phase1_roles.sql)."]
    found = []
    cursor.execute(_NOT_OWNED, [OWNER_ROLE])
    stray = [name for name, _ in cursor.fetchall()]
    if stray:
        found.append(f"{len(stray)} object(s) not owned by {OWNER_ROLE}, e.g. {', '.join(stray[:5])}.")
    for table in sorted(_existing_tables(cursor, APPEND_ONLY_TABLES)):
        cursor.execute("SELECT has_table_privilege(%s, %s, 'UPDATE')", [RUNTIME_ROLE, f"public.{table}"])
        if cursor.fetchone()[0] and table != AUDIT_LOG:
            found.append(f"{RUNTIME_ROLE} can UPDATE {table}.")
    if _existing_tables(cursor, [CHANGE_LOG]):
        cursor.execute("SELECT has_table_privilege(%s, %s, 'INSERT')", [RUNTIME_ROLE, f"public.{CHANGE_LOG}"])
        if cursor.fetchone()[0]:
            found.append(f"{RUNTIME_ROLE} can INSERT into {CHANGE_LOG}.")
    for table in sorted(_existing_tables(cursor, MONITORED_TABLES + APPEND_ONLY_TABLES)):
        cursor.execute("SELECT has_table_privilege(%s, %s, 'TRUNCATE')", [RUNTIME_ROLE, f"public.{table}"])
        if cursor.fetchone()[0]:
            found.append(f"{RUNTIME_ROLE} can TRUNCATE {table}.")
    return found
