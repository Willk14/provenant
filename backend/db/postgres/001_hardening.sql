-- Postgres-only hardening, applied after the app has created its tables.
-- Run as the table owner:  psql "$DATABASE_URL" -f db/postgres/001_hardening.sql
-- The app must connect as a role that does NOT own these tables and is not a superuser.
-- shortcut: hand-applied SQL; move to Alembic migrations before the first shared environment.

-- 1. Tenant isolation. The app sets app.tenant_id per transaction (see api/db.py).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['documents', 'extracted_fields', 'review_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I
         USING (tenant_id = current_setting(''app.tenant_id'', true))
         WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', t);
  END LOOP;
END $$;

-- 2. The review log is append-only, even for someone with SQL access as the app role.
CREATE OR REPLACE FUNCTION provenant_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS review_events_append_only ON review_events;
CREATE TRIGGER review_events_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON review_events
  FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation();
