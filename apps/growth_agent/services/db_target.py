"""
Which database is this process about to touch, and as which role?

Used by ``check_db_target`` as the gate before any migration. Everything here
is read-only, and a password is never read, stored or rendered.

The role rules mirror the Phase 1 role separation: migrations run as
``signalor_owner``; web, workers and cron run as ``signalor_app``, which must
not be a superuser, must not own a monitored table and must not be able to
become the owner.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

OWNER_ROLE = "signalor_owner"
RUNTIME_ROLE = "signalor_app"
ROLE_OWNER = "owner"
ROLE_RUNTIME = "runtime"
ROLE_CHOICES = (ROLE_OWNER, ROLE_RUNTIME)
EXPECTED_ROLE_NAME = {ROLE_OWNER: OWNER_ROLE, ROLE_RUNTIME: RUNTIME_ROLE}

POSTGRES = "postgresql"
NEON_SUFFIX = ".neon.tech"
NEON_POOLER_SUFFIX = "-pooler"

# Tables whose triggers carry the Phase 1 safety guarantees. The runtime role
# must own none of them, or it could disable those triggers.
MONITORED_TABLES = (
    "organizations_organization",
    "integrations_integration",
    "github_agent_githubinstallation",
    "growth_agent_growthprogram",
    "growth_agent_growthcycle",
    "growth_agent_outreachcontact",
    "growth_agent_outreachthread",
)


@dataclass(frozen=True)
class Target:
    """Where a connection points. Deliberately has no credential fields."""

    vendor: str
    host: str
    port: str
    name: str
    role: str
    neon_endpoint: str


@dataclass(frozen=True)
class RoleFacts:
    name: str
    is_superuser: bool
    owned_tables: list[str] = field(default_factory=list)
    member_of: list[str] = field(default_factory=list)


def _neon_endpoint(host: str) -> str:
    if not host.endswith(NEON_SUFFIX):
        return ""
    return host.split(".", 1)[0].removesuffix(NEON_POOLER_SUFFIX)


def describe(settings_dict: dict, *, vendor: str, current_role: str) -> Target:
    host = str(settings_dict.get("HOST") or "").strip().lower()
    return Target(
        vendor=vendor,
        host=host,
        port=str(settings_dict.get("PORT") or ""),
        name=str(settings_dict.get("NAME") or ""),
        role=current_role,
        neon_endpoint=_neon_endpoint(host),
    )


def render(target: Target) -> list[str]:
    lines = [
        f"engine:   {target.vendor}",
        f"host:     {target.host or '(none)'}",
        f"port:     {target.port or '(default)'}",
        f"database: {target.name}",
        f"role:     {target.role or '(not applicable)'}",
    ]
    if target.neon_endpoint:
        lines.append(f"neon:     {target.neon_endpoint}")
    return lines


def fingerprint(target: Target) -> str:
    """Short stable id for a target, safe to log next to a snapshot reference."""
    material = "|".join((target.vendor, target.host, target.port, target.name))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def target_problems(
    target: Target, *, expect_host: str | None, expect_db: str | None, snapshot_ref: str | None
) -> list[str]:
    """Why this target must not be migrated. An absent expectation is a problem,
    never a wildcard: the operator has to state what they believe they are touching."""
    problems = []
    if expect_host is None:
        problems.append("--expect-host is required.")
    elif expect_host.strip().lower() != target.host:
        problems.append(f"Host is '{target.host}', expected '{expect_host}'.")
    if expect_db is None:
        problems.append("--expect-db is required.")
    elif expect_db != target.name:
        problems.append(f"Database is '{target.name}', expected '{expect_db}'.")
    if not (snapshot_ref or "").strip():
        problems.append("--snapshot-ref is required: take a backup or snapshot first and name it.")
    return problems


def role_problems(expected: str, facts: RoleFacts | None) -> list[str]:
    """Why the connected role is not the expected one. Fails closed without facts."""
    if facts is None:
        return ["The role could not be checked on this database (PostgreSQL is required)."]
    wanted = EXPECTED_ROLE_NAME[expected]
    problems = []
    if facts.name != wanted:
        problems.append(f"Connected as '{facts.name}', expected '{wanted}'.")
    if facts.is_superuser:
        problems.append(f"'{facts.name}' is a superuser.")
    if expected == ROLE_RUNTIME:
        if facts.owned_tables:
            problems.append(f"'{facts.name}' owns monitored tables: {', '.join(sorted(facts.owned_tables))}.")
        if OWNER_ROLE in facts.member_of:
            problems.append(f"'{facts.name}' is a member of '{OWNER_ROLE}' and could act as it.")
    return problems


_MEMBERSHIP_SQL = """
    WITH RECURSIVE granted(roleid) AS (
        SELECT m.roleid FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = current_user
        UNION
        SELECT m.roleid FROM pg_auth_members m JOIN granted g ON g.roleid = m.member
    )
    SELECT r.rolname FROM pg_roles r JOIN granted g ON g.roleid = r.oid
"""


def read_current_role(connection) -> str:
    if connection.vendor != POSTGRES:
        return ""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        return cursor.fetchone()[0]


def read_role_facts(connection) -> RoleFacts | None:
    """Read-only facts about the connected role. None where roles do not exist."""
    if connection.vendor != POSTGRES:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
        name, is_superuser = cursor.fetchone()
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE tableowner = current_user AND tablename = ANY(%s)",
            [list(MONITORED_TABLES)],
        )
        owned = [row[0] for row in cursor.fetchall()]
        cursor.execute(_MEMBERSHIP_SQL)
        member_of = [row[0] for row in cursor.fetchall()]
    return RoleFacts(name=name, is_superuser=bool(is_superuser), owned_tables=owned, member_of=member_of)
