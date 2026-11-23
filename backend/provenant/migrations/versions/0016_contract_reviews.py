"""Contract review: firm playbooks, agent reviews, cited findings and follow-up questions.

Revision ID: 0016
Revises: 0015
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TABLES = ("legal_playbooks", "contract_reviews", "contract_findings", "contract_questions")


def upgrade() -> None:
    op.create_table(
        "legal_playbooks",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("positions", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "contract_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("playbook_version", sa.String(32), nullable=False),
        sa.Column("playbook_sha256", sa.String(64), nullable=False),
        sa.Column("playbook", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("not_found", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "contract_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("topic", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("top", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("bottom", sa.Float(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("proposed_term", sa.String(64), nullable=True),
        sa.Column("proposed_value", sa.String(200), nullable=True),
        sa.Column("decision", sa.String(8), nullable=True),
        sa.Column("decision_note", sa.String(1000), nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("field_id", sa.String(36), nullable=True),
    )
    op.create_table(
        "contract_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("question", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("not_stated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("citations", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("asked_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
        if role:
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
