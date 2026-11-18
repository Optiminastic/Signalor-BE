"""
SQL for the safety-change triggers (used by migration 0024). Postgres only.

A trigger on each monitored table writes a ``growth_agent_growthsafetychange``
row whenever a monitored column changes, whatever code path changed it, with
SHA-256 digests of the old and new values, a display form (mailboxes masked),
and the actor from ``signalor.actor`` (``unknown`` when the writer set none).
Nothing is recorded for a brand that is being erased in the same transaction.
"""

FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION growth_safety_insert(
    org bigint, entity text, entity_id bigint, provider text, field text,
    old_value text, new_value text, mask boolean
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    erasing text := coalesce(current_setting('signalor.control_erasure', true), '');
BEGIN
    IF org IS NULL THEN
        RETURN;
    END IF;
    IF erasing <> '' AND (',' || erasing || ',') LIKE ('%,' || org::text || ',%') THEN
        RETURN;  -- the brand itself is being deleted
    END IF;
    INSERT INTO growth_agent_growthsafetychange (
        organization_id, entity, entity_id, provider, field,
        old_digest, new_digest, old_display, new_display, actor, created_at
    ) VALUES (
        org, entity, entity_id, coalesce(provider, ''), field,
        CASE WHEN old_value IS NULL THEN '' ELSE encode(sha256(convert_to(old_value, 'UTF8')), 'hex') END,
        CASE WHEN new_value IS NULL THEN '' ELSE encode(sha256(convert_to(new_value, 'UTF8')), 'hex') END,
        left(CASE WHEN mask AND position('@' in coalesce(old_value, '')) > 0
             THEN left(old_value, 1) || '***@' || split_part(old_value, '@', 2)
             ELSE coalesce(old_value, '') END, 300),
        left(CASE WHEN mask AND position('@' in coalesce(new_value, '')) > 0
             THEN left(new_value, 1) || '***@' || split_part(new_value, '@', 2)
             ELSE coalesce(new_value, '') END, 300),
        coalesce(nullif(current_setting('signalor.actor', true), ''), 'unknown'),
        now()
    );
END;
$$;

CREATE OR REPLACE FUNCTION growth_watch_organization() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.owner_email IS DISTINCT FROM OLD.owner_email THEN
        PERFORM growth_safety_insert(NEW.id, 'organization', NEW.id, '', 'owner_email',
                                     OLD.owner_email, NEW.owner_email, true);
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION growth_watch_integration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    keys text[];
    k text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.provider IN ('gmail', 'wordpress') THEN
            PERFORM growth_safety_insert(OLD.organization_id, 'integration', OLD.id, OLD.provider,
                                         'deleted', 'present', NULL, false);
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.provider NOT IN ('gmail', 'wordpress') AND OLD.provider NOT IN ('gmail', 'wordpress') THEN
        RETURN NEW;
    END IF;
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'integration', NEW.id, NEW.provider, 'organization_id',
                                     OLD.organization_id::text, NEW.organization_id::text, false);
        PERFORM growth_safety_insert(OLD.organization_id, 'integration', NEW.id, NEW.provider, 'organization_id',
                                     OLD.organization_id::text, NEW.organization_id::text, false);
    END IF;
    IF NEW.provider IS DISTINCT FROM OLD.provider THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'integration', NEW.id, NEW.provider, 'provider',
                                     OLD.provider, NEW.provider, false);
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'integration', NEW.id, NEW.provider, 'is_active',
                                     OLD.is_active::text, NEW.is_active::text, false);
    END IF;
    keys := CASE WHEN NEW.provider = 'gmail' THEN ARRAY['email']
                 ELSE ARRAY['site_url', 'blog_id', 'is_wpcom', 'username'] END;
    FOREACH k IN ARRAY keys LOOP
        IF (NEW.metadata ->> k) IS DISTINCT FROM (OLD.metadata ->> k) THEN
            PERFORM growth_safety_insert(NEW.organization_id, 'integration', NEW.id, NEW.provider,
                                         'metadata.' || k, OLD.metadata ->> k, NEW.metadata ->> k, k = 'email');
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION growth_watch_github() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM growth_safety_insert(OLD.organization_id, 'github_installation', OLD.id, 'github',
                                     'deleted', 'present', NULL, false);
        RETURN OLD;
    END IF;
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'github_installation', NEW.id, 'github',
                                     'organization_id', OLD.organization_id::text, NEW.organization_id::text, false);
        PERFORM growth_safety_insert(OLD.organization_id, 'github_installation', NEW.id, 'github',
                                     'organization_id', OLD.organization_id::text, NEW.organization_id::text, false);
    END IF;
    IF NEW.installation_id IS DISTINCT FROM OLD.installation_id THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'github_installation', NEW.id, 'github',
                                     'installation_id', OLD.installation_id::text, NEW.installation_id::text, false);
    END IF;
    IF NEW.repo_full_name IS DISTINCT FROM OLD.repo_full_name THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'github_installation', NEW.id, 'github',
                                     'repo_full_name', OLD.repo_full_name, NEW.repo_full_name, false);
    END IF;
    IF NEW.default_branch IS DISTINCT FROM OLD.default_branch THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'github_installation', NEW.id, 'github',
                                     'default_branch', OLD.default_branch, NEW.default_branch, false);
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        PERFORM growth_safety_insert(NEW.organization_id, 'github_installation', NEW.id, 'github',
                                     'is_active', OLD.is_active::text, NEW.is_active::text, false);
    END IF;
    RETURN NEW;
END;
$$;
"""

WATCHERS = (
    ("organizations_organization", "growth_watch_organization", "AFTER UPDATE"),
    ("integrations_integration", "growth_watch_integration", "AFTER UPDATE OR DELETE"),
    ("github_agent_githubinstallation", "growth_watch_github", "AFTER UPDATE OR DELETE"),
)

APPEND_ONLY = (
    "growth_agent_growthsafetychange",
    "growth_agent_growthsafetyreview",
    "growth_agent_safetyconfirmationevent",
)


def install(cursor) -> None:
    cursor.execute(FUNCTIONS)
    for table, function, when in WATCHERS:
        cursor.execute(f"DROP TRIGGER IF EXISTS growth_safety_watch ON {table}")
        cursor.execute(
            f"CREATE TRIGGER growth_safety_watch {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    for table in APPEND_ONLY:
        cursor.execute(f"DROP TRIGGER IF EXISTS growth_append_only ON {table}")
        cursor.execute(
            f"CREATE TRIGGER growth_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION growth_append_only('organization_id')"
        )


def remove(cursor) -> None:
    for table, _, _ in WATCHERS:
        cursor.execute(f"DROP TRIGGER IF EXISTS growth_safety_watch ON {table}")
    for table in APPEND_ONLY:
        cursor.execute(f"DROP TRIGGER IF EXISTS growth_append_only ON {table}")
    for function in ("growth_watch_organization", "growth_watch_integration", "growth_watch_github"):
        cursor.execute(f"DROP FUNCTION IF EXISTS {function}()")
    cursor.execute(
        "DROP FUNCTION IF EXISTS growth_safety_insert(bigint, text, bigint, text, text, text, text, boolean)"
    )
