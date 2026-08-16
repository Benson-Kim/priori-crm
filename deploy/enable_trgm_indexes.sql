-- Catch-up script: create the pg_trgm extension and every trigram search index.
--
-- WHY THIS EXISTS
-- The migrations that create these indexes are guarded on pg_trgm being
-- available, so a host without the postgresql-contrib package migrates cleanly
-- instead of failing (see the guards in
-- 2026_05_14_1214-7d2461c6487d_add_customer_indexes.py and
-- 2026_06_04_1200-d4e5f6a7b8c9_add_trgm_search_indexes.py).
--
-- Alembic records those migrations as applied either way, so they will NOT
-- re-run once contrib is installed later. This script closes that gap: run it
-- once, after the extension becomes available, to bring the database up to the
-- index set production has.
--
-- Safe to run repeatedly and safe to run when the extension is still missing —
-- it reports and exits rather than failing.
--
-- Usage:
--   psql -h localhost -U <dbuser> -d <dbname> -f deploy/enable_trgm_indexes.sql

DO $trgm$
DECLARE
    idx  record;
    made int := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
        RAISE NOTICE 'pg_trgm is still not available on this server.';
        RAISE NOTICE 'Ask the host to install the postgresql-contrib package, then re-run.';
        RETURN;
    END IF;

    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    -- (index name, table, column) — mirrors _TRGM_INDEXES in the migration.
    FOR idx IN
        SELECT * FROM (VALUES
            ('ix_invoices_invoice_number_trgm',    'invoices',  'invoice_number'),
            ('ix_invoices_invoice_reference_trgm', 'invoices',  'invoice_reference'),
            ('ix_quotes_quote_number_trgm',        'quotes',    'quote_number'),
            ('ix_quotes_quote_reference_trgm',     'quotes',    'quote_reference'),
            ('ix_expenses_expense_number_trgm',    'expenses',  'expense_number'),
            ('ix_expenses_expense_reference_trgm', 'expenses',  'expense_reference'),
            ('ix_vendors_vendor_name_trgm',        'vendors',   'vendor_name'),
            ('ix_vendors_email_trgm',              'vendors',   'email'),
            ('ix_customers_first_name_trgm',       'customers', 'first_name'),
            ('ix_customers_last_name_trgm',        'customers', 'last_name'),
            ('ix_customers_company_name_trgm',     'customers', 'company_name'),
            ('ix_customers_email_trgm',            'customers', 'email')
        ) AS t(index_name, table_name, column_name)
    LOOP
        -- Skip tables that do not exist yet rather than aborting the whole run.
        IF to_regclass(format('public.%I', idx.table_name)) IS NULL THEN
            RAISE NOTICE 'skipping %: table % does not exist', idx.index_name, idx.table_name;
            CONTINUE;
        END IF;

        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I USING gin (%I gin_trgm_ops)',
            idx.index_name, idx.table_name, idx.column_name
        );
        made := made + 1;
    END LOOP;

    -- The composite trigram index from the customer-indexes migration.
    IF to_regclass('public.customers') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS ix_customers_search
            ON customers USING gin (
                first_name gin_trgm_ops,
                last_name gin_trgm_ops,
                company_name gin_trgm_ops
            );
        made := made + 1;
    END IF;

    RAISE NOTICE 'pg_trgm enabled; ensured % index(es).', made;
END
$trgm$;

-- Confirm afterwards:
--   SELECT indexname FROM pg_indexes WHERE indexname LIKE '%trgm%' OR indexname = 'ix_customers_search';
