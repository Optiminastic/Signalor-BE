-- 2026-11-18: 47 migrations from feat/growth-agent-main were applied to the shared
-- Neon staging database by mistake (a local migrate whose DATABASE_URL was
-- overridden by .env). The code deployed to staging (origin/staging) doesn't know
-- six of the new NOT NULL columns, so its INSERTs into these tables fail.
--
-- This gives those columns database defaults equal to the model defaults, so
-- the old staging code and the new code both work. It only adds defaults:
-- no data is changed or removed, and it can be undone with DROP DEFAULT.
--
-- NOT YET RUN. Review, then run once against Neon staging:
--   psql "<neon staging URL>" -v ON_ERROR_STOP=1 -f docs/incidents/2026-11-18-neon-unapproved-migrations.sql

BEGIN;
ALTER TABLE analyzer_recommendation ALTER COLUMN affected_pages SET DEFAULT '[]'::jsonb;
ALTER TABLE analyzer_recommendation ALTER COLUMN evidence SET DEFAULT '{}'::jsonb;
ALTER TABLE analyzer_recommendation ALTER COLUMN generated_content SET DEFAULT '{}'::jsonb;
ALTER TABLE analyzer_useraction ALTER COLUMN verification_message SET DEFAULT '';
ALTER TABLE github_agent_githubinstallation ALTER COLUMN repo_detection SET DEFAULT '{}'::jsonb;
ALTER TABLE github_agent_githubinstallation ALTER COLUMN repo_locked SET DEFAULT false;
COMMIT;

-- Undo (only if wanted later):
--   ALTER TABLE analyzer_recommendation ALTER COLUMN affected_pages DROP DEFAULT;  (and the same for the other five)
