-- Phase 1 database role separation.
--
-- DO NOT RUN until the verification steps in
-- docs/implementation/PHASE_1_CONTROL_FOUNDATION.md are complete for this
-- environment: the named database administrator, the role the application
-- connects as today, that role's attributes and memberships, every runtime
-- connection path, and a verified backup.
--
-- What this does
--   Creates two roles and the grants between them. It moves no table and
--   changes no data. Table ownership is transferred later, by the Phase 1
--   migration, which runs as signalor_owner.
--
--   signalor_owner  migrations only. Owns the monitored and protected tables.
--   signalor_app    web, workers, cron and management commands at runtime.
--                   Must never be a superuser, never own a monitored table,
--                   and never be a member of signalor_owner.
--
-- How to run
--   As a database administrator, once per environment:
--     psql "<admin connection>" -v ON_ERROR_STOP=1 -f deploy/sql/phase1_roles.sql
--   Then set each role's password out of band (\password signalor_owner and
--   \password signalor_app). No password is, or may be, written in this file.
--
--   On Neon, create the roles with this script and not through the console:
--   console-created roles are members of neon_superuser.
--
-- Safe to run twice: every statement is idempotent.
--
-- Afterwards, verify from the application's environment:
--   python manage.py check_db_target --expect-host ... --expect-db ... \
--       --snapshot-ref ... --role runtime      (with the runtime URL)
--   python manage.py check_db_target ... --role owner   (with the migration URL)

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signalor_app') THEN
        CREATE ROLE signalor_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signalor_owner') THEN
        CREATE ROLE signalor_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

-- Re-assert the attributes on every run, so a role that drifted is corrected.
ALTER ROLE signalor_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE signalor_owner NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- The owner must be able to alter tables the application role owns today, and
-- to take them over. Membership runs one way only: the application role is
-- never granted the owner role.
GRANT signalor_app TO signalor_owner;
REVOKE signalor_owner FROM signalor_app;

-- Schema access. Only the owner may create objects.
GRANT USAGE ON SCHEMA public TO signalor_app;
GRANT USAGE, CREATE ON SCHEMA public TO signalor_owner;
REVOKE CREATE ON SCHEMA public FROM signalor_app;

-- Tables and sequences the owner creates from now on are usable by the
-- application without a per-migration grant. TRUNCATE, TRIGGER and REFERENCES
-- are deliberately not granted. The Phase 1 migration narrows these further
-- for the protected (append-only) tables.
ALTER DEFAULT PRIVILEGES FOR ROLE signalor_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signalor_app;
ALTER DEFAULT PRIVILEGES FOR ROLE signalor_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO signalor_app;

-- Report what was set up, so the administrator can record it.
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication
FROM pg_roles
WHERE rolname IN ('signalor_app', 'signalor_owner')
ORDER BY rolname;
