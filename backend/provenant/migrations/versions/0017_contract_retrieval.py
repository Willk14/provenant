"""Contract retrieval: page-located chunks with embeddings (pgvector on Postgres) and their index state.

Exact search within a tenant is deliberate: an approximate index combined with a tenant filter can miss a clause
without saying so. Add one only when volumes require it and recall has been measured.

Revision ID: 0017
Revises: 0016
"""
import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = ("retrieval_indexes", "document_chunks")
DIMENSIONS = 1024


def _vector_schema(bind) -> str:
    """The schema holding pgvector, enabling it if needed. Hosted databases often keep it outside `public`."""
    find = sa.text("select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace "
                   "where e.extname = 'vector'")
    if schema := bind.execute(find).scalar():
        return schema
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION vector"))
    except DBAPIError as exc:
        raise RuntimeError("Postgres needs the pgvector extension. Enable `vector` as an administrator (on Supabase: "
                           "Database, Extensions), or use a pgvector image, then migrate again.") from exc
    return bind.execute(find).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    op.create_table(
        "retrieval_indexes",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("top", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("bottom", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=True),  # SQLite only; Postgres uses the vector column
        sa.UniqueConstraint("document_id", "model", "ordinal"),
    )
    op.create_index("ix_document_chunks_scope", "document_chunks", ["tenant_id", "model", "document_id"])
    if not postgres:
        return
    schema = _vector_schema(bind)
    op.execute(f'ALTER TABLE document_chunks ADD COLUMN embedding "{schema}".vector({DIMENSIONS}) NOT NULL')
    op.execute("CREATE INDEX ix_document_chunks_text ON document_chunks USING gin (to_tsvector('english', text))")
    role = os.environ.get("PROVENANT_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("PROVENANT_APP_ROLE must be a plain lowercase identifier")
    if role:
        op.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}')
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
