"""Claude contract chats, private to their author, with append-only messages (ADR 0016).

Revision ID: 0020
Revises: 0019
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = ("contract_chats", "contract_chat_messages")


def upgrade() -> None:
    op.create_table(
        "contract_chats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # idle | queued | running | failed
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "contract_chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("contract_chats.id"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # user | assistant
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("citations", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("not_stated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("author", sa.String(200), nullable=False),  # the person, or the agent's name
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "position"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
        )
    op.execute("""CREATE TRIGGER contract_chat_messages_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON contract_chat_messages
        FOR EACH STATEMENT EXECUTE FUNCTION provenant_forbid_mutation()""")
    if role:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON contract_chats TO {role}")
        op.execute(f"GRANT SELECT, INSERT ON contract_chat_messages TO {role}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS contract_chat_messages_append_only ON contract_chat_messages")
    for table in reversed(TABLES):
        op.drop_table(table)
