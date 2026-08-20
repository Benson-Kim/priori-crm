-- app_migrator / app_runtime role split (ADR-0013, pulled forward to
-- Phase T1 per the !77 reviews; rollout tracked in issue #80).
--
-- WHY: RLS policies do NOT bind a table's owner unless FORCE ROW LEVEL
-- SECURITY is set — and today the single application role owns every
-- table, so an RLS backstop added later would be a no-op for exactly the
-- connections that matter. Splitting ownership (app_migrator) from
-- request traffic (app_runtime) BEFORE any tenant keys exist makes every
-- later phase's forgotten-WHERE-clause bug a 500/zero rows instead of a
-- cross-tenant data breach.
--
-- HOW TO USE: run as a superuser/DBA role, once per environment, with
-- the passwords substituted from the secret store (never commit real
-- passwords). This script only CREATES the roles and default privileges;
-- the ownership transfer and connection-string cutover are a coordinated
-- maintenance step — follow issue #80's design sketch and checklist.
-- Idempotence: guarded so a re-run does not error.

-- 1) Roles ------------------------------------------------------------
-- (psql does NOT substitute :'variables' inside dollar-quoted DO blocks,
-- so the roles are created passwordless in the guarded block and the
-- passwords are set immediately after, where substitution works.
-- Set the variables with \prompt from an interactive psql session — NOT
-- with `psql -v migrator_password=…` on the command line, which exposes
-- the passwords to every local account via argv/`ps` and the shell
-- history (same rule as deploy/production_release.sh). Invoke:
--   psql -d <app db>
--     \prompt 'app_migrator password: ' migrator_password
--     \prompt 'app_runtime password: '  runtime_password
--     \i docs/operations/sql/create-db-roles.sql
-- )

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_migrator') THEN
        -- Owns all application tables/sequences. The ONLY role Alembic
        -- runs as (deploy pipeline); never used by request traffic.
        CREATE ROLE app_migrator
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
        -- The API's connection role: no ownership, no BYPASSRLS, table
        -- privileges granted explicitly. RLS (once policies exist) binds
        -- this role even without FORCE — FORCE stays on regardless, as
        -- belt-and-braces against ownership drift.
        CREATE ROLE app_runtime
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

ALTER ROLE app_migrator PASSWORD :'migrator_password';
ALTER ROLE app_runtime PASSWORD :'runtime_password';

-- 2) Privileges for existing objects (run in the application database) --

GRANT USAGE ON SCHEMA public TO app_migrator, app_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;

-- 3) Default privileges for FUTURE objects created by app_migrator ------
-- (i.e. every table a later Alembic migration creates is automatically
-- usable by the runtime role — no per-migration GRANT boilerplate).

ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- 4) Ownership transfer (maintenance window; see issue #80) -------------
-- Run as the CURRENT owner of the tables (or a superuser):
--
--   REASSIGN OWNED BY <current_app_role> TO app_migrator;
--
-- 5) Verification -------------------------------------------------------
-- Must return zero rows after the transfer:
--
--   SELECT tablename, tableowner FROM pg_tables
--   WHERE schemaname = 'public' AND tableowner <> 'app_migrator';
--
-- And the runtime role must never gain dangerous attributes:
--
--   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
--   WHERE rolname IN ('app_migrator', 'app_runtime');
--   -- expect: f / f for both.
--
-- 6) After the split is live (issue #80): revoke audit-trail mutation
-- from the runtime role, layered on the append-only trigger — run
-- revoke-audit-events-mutation.sql (same directory; explains why it is
-- documented SQL, not a migration) and its verification queries.
