"""Preparer inputs on extracted fields: a stated basis for values the document does not state (ADR 0005).

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("extracted_fields") as batch:
        batch.add_column(sa.Column("basis", sa.Text(), nullable=True))
    # A field either cites a location (page >= 1) or is a preparer input (page 0) with a basis.
    with op.batch_alter_table("extracted_fields") as batch:
        batch.create_check_constraint(
            "ck_extracted_fields_input_has_basis", "page >= 1 OR (page = 0 AND basis IS NOT NULL AND basis <> '')"
        )


def downgrade() -> None:
    with op.batch_alter_table("extracted_fields") as batch:
        batch.drop_constraint("ck_extracted_fields_input_has_basis", type_="check")
        batch.drop_column("basis")
