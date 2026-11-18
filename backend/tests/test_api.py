import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from openpyxl import load_workbook

from provenant.api.main import create_app
from provenant.worker import drain


def who(user: str, roles: str, tenant: str = "firm-a") -> dict[str, str]:
    return {"X-Tenant-Id": tenant, "X-User": user, "X-Roles": roles}


PREPARER = who("pat@firm-a", "preparer")
REVIEWER = who("rae@firm-a", "reviewer")
PARTNER = who("pam@firm-a", "partner")
OTHER_FIRM = who("bob@firm-b", "preparer,reviewer,partner", tenant="firm-b")

LEASE_LINES = [
    "LEASE AGREEMENT",
    "Commencement Date: 31 January 2026",
    "Lease Term: 12 months",
    "Monthly Rent: KES 1,000.00 payable monthly in arrears",
    "Discount Rate: 12%",
]


def make_pdf(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def run_jobs(app) -> int:
    return drain(app.state.session_factory, app.state.store, app.state.extractor)


def upload(client, data: bytes, headers=PREPARER):
    return client.post("/documents", headers=headers, files={"file": ("lease.pdf", data, "application/pdf")})


def uploaded_lease(client, app, lines=LEASE_LINES) -> tuple[str, dict]:
    doc_id = upload(client, make_pdf(lines)).json()["id"]
    run_jobs(app)
    doc = client.get(f"/documents/{doc_id}", headers=PREPARER).json()
    return doc_id, {f["name"]: f for f in doc["fields"]}


def review(client, field_id, headers, action="approved", corrected_value=None):
    body = {"action": action} | ({"corrected_value": corrected_value} if corrected_value else {})
    return client.post(f"/fields/{field_id}/review", headers=headers, json=body)


def test_requires_identity(client):
    assert client.get("/review-queue").status_code == 401
    assert client.get("/review-queue", headers={"X-Tenant-Id": "bad tenant!", "X-User": "x"}).status_code == 401
    assert client.get("/review-queue", headers=who("x", "intern")).status_code == 403


def test_rejects_non_pdf(client):
    r = upload(client, b"hello")
    assert r.status_code == 422 and "PDF" in r.json()["detail"]


def test_upload_queues_then_worker_extracts(client, app):
    doc_id = upload(client, make_pdf(LEASE_LINES)).json()["id"]
    assert client.get(f"/documents/{doc_id}", headers=PREPARER).json()["status"] == "queued"
    assert run_jobs(app) == 1
    assert client.get(f"/documents/{doc_id}", headers=PREPARER).json()["status"] == "extracted"
    assert run_jobs(app) == 0  # nothing left


def test_lease_from_upload_to_schedule(client, app):
    doc_id, fields = uploaded_lease(client, app)
    assert {k: v["value"] for k, v in fields.items()} == {
        "commencement_date": "2026-01-31",
        "term_months": "12",
        "monthly_payment": "1,000.00",
        "annual_discount_rate": "12%",
        "payment_timing": "arrears",
    }
    rent = fields["monthly_payment"]
    assert rent["page"] == 1 and rent["status"] == "auto_accepted"
    x0, top, x1, bottom = rent["bbox"]
    assert 0 < x0 < x1 and 0 < top < bottom

    queue = client.get("/review-queue", headers=REVIEWER).json()
    assert {f["name"] for f in queue} == {"annual_discount_rate", "payment_timing"}
    blocked = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER)
    assert blocked.status_code == 409 and "not verified" in blocked.json()["detail"]

    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 200
    typo = review(client, fields["annual_discount_rate"]["id"], PARTNER, "corrected", "12")
    assert typo.status_code == 422  # the correction is validated too, and nothing was logged
    r = review(client, fields["annual_discount_rate"]["id"], PARTNER, "corrected", "12%")
    assert r.status_code == 200

    sched = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).json()
    assert Decimal(sched["initial_liability"]) == Decimal("11255.08")
    assert Decimal(sched["periods"][-1]["closing"]) == 0
    assert client.get("/review-log/verify", headers=PREPARER).json() == {"intact": True, "events": 2}

    xlsx = client.get(f"/documents/{doc_id}/lease-schedule.xlsx", headers=PREPARER)
    wb = load_workbook(io.BytesIO(xlsx.content))
    assert wb.sheetnames == ["Schedule", "Journal", "Chain", "Provenance"]
    assert wb["Schedule"]["B1"].value == 11255.08
    assert wb["Provenance"].max_row == 6


def test_roles_and_maker_checker(client, app):
    _, fields = uploaded_lease(client, app)
    timing = fields["payment_timing"]["id"]
    assert review(client, timing, PREPARER).status_code == 403  # preparers cannot review
    assert review(client, timing, REVIEWER).status_code == 403  # judgement fields need a partner
    uploader_as_partner = who("pat@firm-a", "partner")
    r = review(client, timing, uploader_as_partner)
    assert r.status_code == 403 and "maker-checker" in r.json()["detail"]
    assert review(client, timing, PARTNER).status_code == 200


def test_duplicate_upload_is_idempotent(client, app):
    data = make_pdf(LEASE_LINES)
    first, second = upload(client, data), upload(client, data)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert run_jobs(app) == 1  # one job, not two


def test_document_list_and_me(client, app):
    doc_id, _ = uploaded_lease(client, app)
    listed = client.get("/documents", headers=PREPARER).json()
    assert [(d["id"], d["needs_review"], d["missing_terms"]) for d in listed] == [(doc_id, 2, 0)]
    short_id, _ = uploaded_lease(client, app, LEASE_LINES[:3])  # rent, timing and rate lines left out
    listed = {d["id"]: d["missing_terms"] for d in client.get("/documents", headers=PREPARER).json()}
    assert listed[short_id] == 3
    assert client.get("/documents", headers=OTHER_FIRM).json() == []
    me = client.get("/me", headers=REVIEWER).json()
    assert me == {"tenant_id": "firm-a", "user": "rae@firm-a", "roles": ["reviewer"]}


def test_tenants_are_isolated(client, app):
    doc_id, fields = uploaded_lease(client, app)
    assert client.get(f"/documents/{doc_id}", headers=OTHER_FIRM).status_code == 404
    assert client.get(f"/documents/{doc_id}/file", headers=OTHER_FIRM).status_code == 404
    assert client.get("/review-queue", headers=OTHER_FIRM).json() == []
    assert review(client, fields["payment_timing"]["id"], OTHER_FIRM).status_code == 404
    assert upload(client, make_pdf(LEASE_LINES), OTHER_FIRM).json()["id"] != doc_id


def test_cannot_review_twice_or_review_auto_accepted(client, app):
    _, fields = uploaded_lease(client, app)
    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 200
    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 409
    assert review(client, fields["monthly_payment"]["id"], PARTNER).status_code == 409


def test_manual_entry_must_cite_the_page_and_be_checked_by_someone_else(client, app):
    lines = LEASE_LINES[:4] + ["The lessee's incremental borrowing rate is 12% per annum."]
    doc_id, fields = uploaded_lease(client, app, lines)
    assert "annual_discount_rate" not in fields  # the label extractor cannot read prose
    rent_box = fields["monthly_payment"]["bbox"]

    def enter(bbox, headers=PREPARER, name="annual_discount_rate", value="12%"):
        body = {"name": name, "value": value, "page": 1, "bbox": bbox}
        return client.post(f"/documents/{doc_id}/fields", headers=headers, json=body)

    assert enter([0, 0, 5, 5]).status_code == 422  # empty box: nothing printed there
    bad = client.post(
        f"/documents/{doc_id}/fields",
        headers=PREPARER,
        json={"name": "annual_discount_rate", "value": "12", "page": 1, "bbox": [0, 0, 600, 900]},
    )
    assert bad.status_code == 422 and "percentage" in bad.json()["detail"]
    assert enter([0, 0, 600, 900], name="monthly_payment", value="1,000.00").status_code == 409  # already extracted
    # A box around the next printed line (lines are 8 mm, about 23 pt, apart).
    line = [0, rent_box[3] + 2, 600, rent_box[3] + 30]
    r = enter(line)
    assert r.status_code == 201
    entered = r.json()
    assert entered["status"] == "needs_review" and entered["entered_by"] == "pat@firm-a"
    assert entered["bbox"][1] > rent_box[3]  # tightened to the printed text on the next line

    assert review(client, entered["id"], who("pat@firm-a", "partner")).status_code == 403  # maker
    assert review(client, entered["id"], PARTNER).status_code == 200
    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 200
    assert client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).status_code == 200


def test_missing_terms_block_the_schedule(client, app):
    doc_id, _ = uploaded_lease(client, app, LEASE_LINES[:3])
    r = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER)
    assert r.status_code == 409 and "missing lease fields" in r.json()["detail"]


def test_failed_extraction_is_recorded_and_reupload_retries(database_url, tmp_path):
    class Broken:
        name = "broken@0"

        def extract(self, pdf, sha, kind="lease"):
            raise RuntimeError("model unavailable")

    data = make_pdf(LEASE_LINES)  # fpdf stamps the creation time, so build the bytes once
    app = create_app(database_url, tmp_path / "s", extractor=Broken(), dev_auth=True)
    with TestClient(app) as c:
        doc_id = upload(c, data).json()["id"]
        assert run_jobs(app) == 1  # first attempt fails, retry is scheduled in the future
        doc = c.get(f"/documents/{doc_id}", headers=PREPARER).json()
        assert doc["status"] == "failed" and "model unavailable" in doc["error"]

    # Re-uploading a failed document queues it again; a working extractor then succeeds.
    app = create_app(database_url, tmp_path / "s", dev_auth=True)
    with TestClient(app) as c:
        assert upload(c, data).json()["id"] == doc_id
        run_jobs(app)
        assert c.get(f"/documents/{doc_id}", headers=PREPARER).json()["status"] == "extracted"


def test_no_auth_configured_refuses_to_start(database_url, tmp_path, monkeypatch):
    monkeypatch.delenv("PROVENANT_DEV_AUTH", raising=False)
    monkeypatch.delenv("PROVENANT_JWT_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="No authentication configured"):
        create_app(database_url, tmp_path / "s")
