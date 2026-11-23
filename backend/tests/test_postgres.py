"""Database-level guarantees. Run with TEST_DATABASE_URL pointing at a Postgres superuser."""
from types import SimpleNamespace

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


def test_tax_approvals_are_isolated_and_append_only(client, app, database_url):
    from tests.test_tax_api import client_with_tb
    from tests.test_api import PREPARER, REVIEWER

    cid = client_with_tb(client)
    client.post(f"/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PREPARER)
    # 5700 is intentionally unmapped and must be explicitly classified before approval.
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    assert client.post(f"/clients/{cid}/years/2026-12-31/tax/approval", headers=REVIEWER).status_code == 200
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from tax_approvals")) == 0
    for statement in ("update tax_approvals set reviewer = 'tampered'", "delete from tax_approvals"):
        with tenant_session(factory, "firm-a") as s, pytest.raises(DBAPIError, match="permission denied|append-only"):
            s.execute(text(statement))


def test_etims_records_are_tenant_isolated(client, app, database_url):
    from tests.test_etims import ETIMS
    from tests.test_etims_api import CLIENT_LEDGER, setup, upload

    _, year = setup(client)
    assert upload(client, f"{year}/etims/ledger", CLIENT_LEDGER, "p.csv").status_code == 201
    assert upload(client, f"{year}/etims/invoices", ETIMS, "e.csv").status_code == 201
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from purchase_lines")) == 7
    with tenant_session(factory, "firm-b") as s:
        for table in ("purchase_ledgers", "purchase_lines", "etims_exports", "etims_invoices", "etims_decisions"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0


def test_variance_records_are_tenant_isolated(client, app, database_url):
    from tests.test_etims_api import setup, upload
    from tests.test_variance_api import VAT

    _, year = setup(client)
    assert upload(client, f"{year}/checks/returns/vat", VAT.encode(), "vat.csv").status_code == 201
    client.put(f"{year}/checks/vat_turnover/explanation", headers={"X-Tenant-Id": "firm-a", "X-User": "pat@firm-a",
               "X-Roles": "preparer"}, json={"text": "timing difference on the December invoice"})
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from compliance_returns")) == 1
    with tenant_session(factory, "firm-b") as s:
        for table in ("compliance_returns", "variance_explanations"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0


def test_statements_are_tenant_isolated(client, app, database_url):
    from tests.test_etims_api import setup
    from tests.test_statements import MPESA
    from tests.test_statements_api import post

    _, year = setup(client)
    assert post(client, year, MPESA, "mpesa", "M-Pesa Till 123456").status_code == 201
    client.post(f"{year}/statements/confirm-suggestions", headers={"X-Tenant-Id": "firm-a", "X-User": "pat@firm-a",
                "X-Roles": "preparer"})
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from statement_categories")) == 4
    with tenant_session(factory, "firm-b") as s:
        for table in ("statements", "statement_categories"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0


def test_memo_drafts_are_tenant_isolated(client, app, database_url):
    from tests.test_etims_api import setup

    _, year = setup(client)
    r = client.put(f"{year}/memo", headers={"X-Tenant-Id": "firm-a", "X-User": "pat@firm-a", "X-Roles": "preparer"},
                   json={"body": "Tax payable: {{tax_payable}}"})
    assert r.status_code == 200, r.text
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from memo_drafts")) == 1
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from memo_drafts")) == 0


def test_ledgers_and_journal_exceptions_are_tenant_isolated(client, app, database_url):
    from tests.test_api import REVIEWER
    from tests.test_journal import GL
    from tests.test_ledger_api import upload
    from tests.test_tax_api import client_with_tb

    year = f"/clients/{client_with_tb(client)}/years/2026-12-31"
    assert upload(client, year, GL.encode()).status_code == 201
    client.put(f"{year}/ledger/exceptions", headers=REVIEWER, json={
        "entry_id": "E5", "test": "unbalanced", "status": "cleared", "reason": "Rounding on a supplier credit note"})
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from journal_exceptions")) == 1
    with tenant_session(factory, "firm-b") as s:
        for table in ("general_ledgers", "journal_exceptions"):
            assert s.scalar(text(f"select count(*) from {table}")) == 0


def test_covenant_period_inputs_are_tenant_isolated(client, app, database_url):
    from tests.test_api import PREPARER
    from tests.test_covenants_api import upload_agreement

    doc_id = upload_agreement(client, app)
    assert client.put(f"/documents/{doc_id}/covenants/periods/2026-12-31", headers=PREPARER, json={
        "revolver_drawn": "1000", "basis": "Agent's statement of 31 December"}).status_code == 200
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from covenant_period_inputs")) == 1
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from covenant_period_inputs")) == 0


def test_contract_chunks_are_tenant_isolated_and_use_pgvector(client, app, database_url):
    from tests.test_api import PREPARER, run_jobs
    from tests.test_covenants_api import upload_agreement

    upload_agreement(client, app)
    client.post("/legal/index", headers=PREPARER)
    run_jobs(app)
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from retrieval_indexes")) == 1
        assert s.scalar(text("select count(*) from document_chunks")) >= 1
        assert s.scalar(text("select format_type(atttypid, atttypmod) from pg_attribute where attrelid = "
                             "'document_chunks'::regclass and attname = 'embedding'")).endswith("vector(1024)")
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from retrieval_indexes")) == 0
        assert s.scalar(text("select count(*) from document_chunks")) == 0


def test_contract_reviews_are_tenant_isolated(client, app, database_url, monkeypatch):
    import provenant.worker as worker
    from tests.test_api import PARTNER, PREPARER, run_jobs
    from tests.test_covenants_api import upload_agreement
    from tests.test_legal_api import LINES, ScriptedAgent

    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    doc_id = upload_agreement(client, app, LINES)
    assert client.put("/legal/playbook", headers=PARTNER, json={
        "positions": {"indemnity": "Our clients give no indemnities."}}).status_code == 200
    review = client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).json()
    run_jobs(app)
    assert client.post(f"/documents/{doc_id}/reviews/{review['id']}/questions", headers=PREPARER,
                       json={"question": "Can they cancel?"}).status_code == 202
    factory = make_sessionmaker(make_engine(database_url))
    tables = ("legal_playbooks", "contract_reviews", "contract_findings", "contract_questions")
    with tenant_session(factory, "firm-a") as s:
        assert all(s.scalar(text(f"select count(*) from {t}")) >= 1 for t in tables)
    with tenant_session(factory, "firm-b") as s:
        assert all(s.scalar(text(f"select count(*) from {t}")) == 0 for t in tables)


def test_ai_runs_are_tenant_isolated_and_append_only(client, app, database_url):
    from provenant.ai import record_call
    from provenant.api.ai_runs import recording

    factory = make_sessionmaker(make_engine(database_url))
    with recording(factory, "firm-a", None):
        record_call("extraction_lease", "claude-opus-5-5", SimpleNamespace(stop_reason="end_turn"), 0.1)
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from ai_runs")) == 1
        for change in ("update ai_runs set status = 'failed'", "delete from ai_runs"):  # no grant, and a trigger too
            with pytest.raises(DBAPIError, match="permission denied|append-only"):
                s.execute(text(change))
            s.rollback()
        assert s.scalar(text("select count(*) from pg_trigger where tgname = 'ai_runs_append_only'")) == 1
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from ai_runs")) == 0


def test_covenant_adjustments_are_tenant_isolated(client, app, database_url):
    from tests.test_api import PREPARER
    from tests.test_covenants_api import upload_agreement, verify_all

    doc_id = upload_agreement(client, app)
    verify_all(client, doc_id)
    assert client.post(f"/documents/{doc_id}/covenants/adjustments", headers=PREPARER, json={
        "kind": "addback", "quarter_end": "2026-12-31", "label": "Restructuring", "amount": "1000",
        "clause": "Clause 1.1", "basis": "Board paper of 14 December"}).status_code == 201
    factory = make_sessionmaker(make_engine(database_url))
    with tenant_session(factory, "firm-a") as s:
        assert s.scalar(text("select count(*) from covenant_adjustments")) == 1
    with tenant_session(factory, "firm-b") as s:
        assert s.scalar(text("select count(*) from covenant_adjustments")) == 0
