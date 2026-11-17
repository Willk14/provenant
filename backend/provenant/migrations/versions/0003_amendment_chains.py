"""Amendment chains: a document is a lease or an amendment that points at its lease by id and SHA-256.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="lease"))
        batch.add_column(sa.Column("parent_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("parent_sha256", sa.String(64), nullable=True))
        batch.create_foreign_key("fk_documents_parent_id", "documents", ["parent_id"], ["id"])
        batch.create_index("ix_documents_parent_id", ["parent_id"])
        batch.create_check_constraint(
            "ck_documents_amendment_has_parent",
            "(kind = 'lease' AND parent_id IS NULL AND parent_sha256 IS NULL)"
            " OR (kind = 'amendment' AND parent_id IS NOT NULL AND parent_sha256 IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_amendment_has_parent", type_="check")
        batch.drop_index("ix_documents_parent_id")
        batch.drop_constraint("fk_documents_parent_id", type_="foreignkey")
        batch.drop_column("parent_sha256")
        batch.drop_column("parent_id")
        batch.drop_column("kind")
