"""Postgres hardening: tenant RLS, append-only review log, least-privilege grants for the app role.

Revision ID: 0002
Revises: 0001

Run migrations as the table owner. The API must connect as a different, non-superuser role
(PROVENANT_APP_ROLE, which must already exist); superusers and BYPASSRLS roles skip RLS entirely.
"""
import os
import re

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("documents", "extracted_fields", "review_events")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _app_role() -> str | None:
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
    return role


def upgrade() -> None:
    if not _postgres():
        return  # SQLite (dev/tests) has no RLS; services scope every query by tenant_id regardless
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
        )

    op.execute(
        """CREATE FUNCTION provenant_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END $$"""
    )
    op.execute(
        """CREATE TRIGGER review_events_append_only
           BEFORE UPDATE OR DELETE OR TRUNCATE ON review_events
           FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()"""
    )

    if role := _app_role():
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON documents, extracted_fields, jobs TO {role}")
        op.execute(f"GRANT SELECT, INSERT ON review_events TO {role}")  # no UPDATE/DELETE, even before the trigger
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}")


def downgrade() -> None:
    if not _postgres():
        return
    op.execute("DROP TRIGGER IF EXISTS review_events_append_only ON review_events")
    op.execute("DROP FUNCTION IF EXISTS provenant_forbid_mutation()")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
