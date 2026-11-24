"""Staff directory: who belongs to a firm, their roles and the email that notifications go to (ADR 0017).

Revision ID: 0021
Revises: 0020
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(200), nullable=True),  # the sign-in subject, once they have signed in
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("roles", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # invited | active
        sa.Column("invited_by", sa.String(200), nullable=True),
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "email"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE members FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON members
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""")
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON members TO {role}")


def downgrade() -> None:
    op.drop_table("members")
