"""Database-level guarantees. Run with TEST_DATABASE_URL pointing at a Postgres superuser."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from provenant.api.db import Document, make_engine, make_sessionmaker, tenant_session
from tests.conftest import PG_ADMIN_URL
from tests.test_api import PARTNER, review, uploaded_lease

pytestmark = pytest.mark.skipif(not PG_ADMIN_URL, reason="needs TEST_DATABASE_URL (Postgres)")


def test_rls_hides_other_tenants_even_without_a_where_clause(client, app, database_url):
    uploaded_lease(client, app)
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert len(s.scalars(select(Document)).all()) == 1
    with tenant_session(factory, "firm-b") as s:
        assert s.scalars(select(Document)).all() == []
        assert s.scalar(text("select count(*) from extracted_fields")) == 0
    with factory() as s:  # no tenant at all
        assert s.scalars(select(Document)).all() == []


def test_rls_blocks_writing_rows_for_another_tenant(database_url):
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        s.add(Document(tenant_id="firm-b", sha256="c" * 64, filename="x.pdf", size_bytes=1, uploaded_by="x"))
        with pytest.raises(DBAPIError, match="row-level security"):
            s.commit()


def test_trial_balances_are_tenant_isolated(client, app, database_url):
    from tests.test_covenants_api import STEADY, tb_csv, upload_agreement, upload_tb

    doc_id = upload_agreement(client, app)
    assert upload_tb(client, doc_id, "2026-03-31", tb_csv(**STEADY)).status_code == 201
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from trial_balance_lines")) == 7
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from trial_balance_lines")) == 0
        assert s.scalar(text("select count(*) from trial_balances")) == 0


def test_client_data_is_isolated_and_mapping_history_is_append_only(client, app, database_url):
    from tests.test_tax_api import client_with_tb

    cid = client_with_tb(client)
    client.put(f"/clients/{cid}/mappings/5700", headers=PARTNER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-b") as s:
        for table in ("clients", "client_tb_lines", "account_mappings", "account_mapping_events"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0
    for statement in ("update account_mapping_events set by = 'someone else'", "delete from account_mapping_events"):
        with tenant_session(factory, "firm-a") as s, pytest.raises(DBAPIError, match="permission denied|append-only"):
            s.execute(text(statement))


def test_review_log_cannot_be_edited_or_deleted(client, app, database_url):
    _, fields = uploaded_lease(client, app)
    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 200
    factory = make_sessionmaker(make_engine(database_url))
    for statement in ("update review_events set after = 'tampered'", "delete from review_events"):
        with tenant_session(factory, "firm-a") as s, pytest.raises(DBAPIError, match="permission denied|append-only"):
            s.execute(text(statement))


def test_tax_records_are_tenant_isolated(client, app, database_url):
    from tests.test_tax_records import REGISTER, ready_client, upload

    cid = ready_client(client)
    assert upload(client, cid, "fixed-assets", REGISTER, "far.csv").status_code == 201
    assert upload(client, cid, "attachments", b"date,amount\n", "bank.csv", category="bank_statement").status_code == 201
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from fixed_assets")) == 4
    with tenant_session(factory, "firm-b") as s:
        for table in ("fixed_asset_registers", "fixed_assets", "client_attachments"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0
