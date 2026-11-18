"""
Database-level protection for the append-only records (Phase 1).

On Postgres, a trigger refuses any UPDATE or DELETE of an action version,
approval decision, execution event, attempt-ledger row or audit-log row. The
model and queryset already refuse this in Django; the trigger also stops raw SQL
and any other code path. TRUNCATE is stopped by privileges instead: the runtime
role is never granted it (role split, ``growth_apply_role_split``).

Two exceptions:
- Deleting a brand: the Organization ``pre_delete`` receiver adds the brand's id
  to the transaction-local setting ``signalor.control_erasure``; rows of that
  brand (and only that brand) may then be deleted, in that transaction.
- The audit log may have ``task_id`` set to NULL (a deleted task), and nothing else.

SQLite has no such triggers; this migration does nothing there.
"""

from django.db import migrations

# table -> how to find the row's organization: a column on the row, or through a parent.
PROTECTED = {
    "growth_agent_actionversion": "action",
    "growth_agent_approvaldecision": "organization_id",
    "growth_agent_actionexecutionevent": "organization_id",
    "growth_agent_dispatchattemptledger": "organization_id",
    "growth_agent_agentactionlog": "program",
}

FUNCTION = r"""
CREATE OR REPLACE FUNCTION growth_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    org_id bigint;
    erasing text := coalesce(current_setting('signalor.control_erasure', true), '');
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF TG_TABLE_NAME = 'growth_agent_agentactionlog'
           AND OLD.task_id IS NOT NULL AND NEW.task_id IS NULL
           AND (to_jsonb(NEW) - 'task_id') = (to_jsonb(OLD) - 'task_id') THEN
            RETURN NEW;  -- the task was deleted: the audit row stays, unlinked
        END IF;
        RAISE EXCEPTION 'growth: % rows are append-only', TG_TABLE_NAME
            USING ERRCODE = 'P0001';
    END IF;
    IF TG_ARGV[0] = 'organization_id' THEN
        org_id := (to_jsonb(OLD) ->> 'organization_id')::bigint;
    ELSIF TG_ARGV[0] = 'action' THEN
        SELECT organization_id INTO org_id FROM growth_agent_growthaction
            WHERE id = (to_jsonb(OLD) ->> 'action_id')::bigint;
    ELSE
        SELECT organization_id INTO org_id FROM growth_agent_growthprogram
            WHERE id = (to_jsonb(OLD) ->> 'program_id')::bigint;
    END IF;
    IF erasing <> '' AND (org_id IS NULL OR (',' || erasing || ',') LIKE ('%,' || org_id::text || ',%')) THEN
        -- Deleting this brand (a parent already removed in the same erasure has no org left).
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'growth: % rows are append-only', TG_TABLE_NAME USING ERRCODE = 'P0001';
END;
$$;
"""


def _install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FUNCTION)
        for table, owner_path in PROTECTED.items():
            cursor.execute(f"DROP TRIGGER IF EXISTS growth_append_only ON {table}")
            cursor.execute(
                f"CREATE TRIGGER growth_append_only BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION growth_append_only('{owner_path}')"
            )


def _remove(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in PROTECTED:
            cursor.execute(f"DROP TRIGGER IF EXISTS growth_append_only ON {table}")
        cursor.execute("DROP FUNCTION IF EXISTS growth_append_only()")


class Migration(migrations.Migration):
    dependencies = [("growth_agent", "0021_brand_mentions")]

    operations = [migrations.RunPython(_install, _remove)]
