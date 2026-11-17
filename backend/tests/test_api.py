import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from openpyxl import load_workbook

from provenant.api.main import create_app

ALICE = {"X-Tenant-Id": "firm-a", "X-User": "alice@firm-a"}
BOB = {"X-Tenant-Id": "firm-b", "X-User": "bob@firm-b"}

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


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}", tmp_path / "storage", dev_auth=True)
    with TestClient(app) as c:
        yield c


def upload(client, data: bytes, headers=ALICE):
    return client.post("/documents", headers=headers, files={"file": ("lease.pdf", data, "application/pdf")})


def fields_by_name(client, doc_id, headers=ALICE):
    doc = client.get(f"/documents/{doc_id}", headers=headers).json()
    return {f["name"]: f for f in doc["fields"]}


def test_requires_identity(client):
    assert client.get("/review-queue").status_code == 401
    assert client.get("/review-queue", headers={"X-Tenant-Id": "bad tenant!", "X-User": "x"}).status_code == 401


def test_rejects_non_pdf(client):
    r = upload(client, b"hello")
    assert r.status_code == 422 and "PDF" in r.json()["detail"]


def test_lease_from_upload_to_schedule(client):
    r = upload(client, make_pdf(LEASE_LINES))
    assert r.status_code == 201
    doc_id = r.json()["id"]

    doc = client.get(f"/documents/{doc_id}", headers=ALICE).json()
    assert doc["status"] == "extracted"
    fields = {f["name"]: f for f in doc["fields"]}
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
    assert 0 < x0 < x1 and 0 < top < bottom  # points at the figure, not the label

    # Judgement fields are held for a human; the core refuses to calculate until they are signed off.
    queue = client.get("/review-queue", headers=ALICE).json()
    assert {f["name"] for f in queue} == {"annual_discount_rate", "payment_timing"}
    blocked = client.get(f"/documents/{doc_id}/lease-schedule", headers=ALICE)
    assert blocked.status_code == 409 and "not verified" in blocked.json()["detail"]

    approve = client.post(f"/fields/{fields['payment_timing']['id']}/review", headers=ALICE, json={"action": "approved"})
    assert approve.status_code == 200 and approve.json()["status"] == "approved"
    correct = client.post(
        f"/fields/{fields['annual_discount_rate']['id']}/review",
        headers=ALICE,
        json={"action": "corrected", "corrected_value": "12%"},
    )
    assert correct.status_code == 200

    sched = client.get(f"/documents/{doc_id}/lease-schedule", headers=ALICE).json()
    assert Decimal(sched["initial_liability"]) == Decimal("11255.08")
    assert Decimal(sched["periods"][-1]["closing"]) == 0
    assert len(sched["periods"]) == 12

    assert client.get("/review-log/verify", headers=ALICE).json() == {"intact": True, "events": 2}

    xlsx = client.get(f"/documents/{doc_id}/lease-schedule.xlsx", headers=ALICE)
    assert xlsx.status_code == 200
    wb = load_workbook(io.BytesIO(xlsx.content))
    assert wb.sheetnames == ["Schedule", "Journal", "Provenance"]
    assert wb["Schedule"]["B1"].value == 11255.08
    assert wb["Provenance"].max_row == 6  # header + 5 fields, each with its document hash and bbox


def test_duplicate_upload_is_idempotent(client):
    data = make_pdf(LEASE_LINES)
    first, second = upload(client, data), upload(client, data)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]


def test_tenants_are_isolated(client):
    doc_id = upload(client, make_pdf(LEASE_LINES)).json()["id"]
    field_id = fields_by_name(client, doc_id)["payment_timing"]["id"]
    assert client.get(f"/documents/{doc_id}", headers=BOB).status_code == 404
    assert client.get(f"/documents/{doc_id}/file", headers=BOB).status_code == 404
    assert client.get("/review-queue", headers=BOB).json() == []
    assert client.post(f"/fields/{field_id}/review", headers=BOB, json={"action": "approved"}).status_code == 404
    # Same file, other firm: a separate document, not a shared one.
    assert upload(client, make_pdf(LEASE_LINES), BOB).json()["id"] != doc_id


def test_cannot_review_twice_or_review_auto_accepted(client):
    doc_id = upload(client, make_pdf(LEASE_LINES)).json()["id"]
    fields = fields_by_name(client, doc_id)
    timing = f"/fields/{fields['payment_timing']['id']}/review"
    assert client.post(timing, headers=ALICE, json={"action": "approved"}).status_code == 200
    assert client.post(timing, headers=ALICE, json={"action": "approved"}).status_code == 409
    rent = f"/fields/{fields['monthly_payment']['id']}/review"
    assert client.post(rent, headers=ALICE, json={"action": "approved"}).status_code == 409


def test_missing_terms_block_the_schedule(client):
    doc_id = upload(client, make_pdf(LEASE_LINES[:3])).json()["id"]
    r = client.get(f"/documents/{doc_id}/lease-schedule", headers=ALICE)
    assert r.status_code == 409 and "missing lease fields" in r.json()["detail"]


def test_extractor_failure_is_recorded_and_retried(tmp_path):
    class Broken:
        name = "broken@0"

        def extract(self, pdf, sha):
            raise RuntimeError("model unavailable")

    data = make_pdf(LEASE_LINES)
    app = create_app(f"sqlite:///{tmp_path / 'f.db'}", tmp_path / "s", extractor=Broken(), dev_auth=True)
    with TestClient(app) as c:
        doc_id = upload(c, data).json()["id"]
        doc = c.get(f"/documents/{doc_id}", headers=ALICE).json()
        assert doc["status"] == "failed" and "model unavailable" in doc["error"]

    # Same database, working extractor: re-uploading the same file retries extraction.
    app = create_app(f"sqlite:///{tmp_path / 'f.db'}", tmp_path / "s", dev_auth=True)
    with TestClient(app) as c:
        assert upload(c, data).json()["id"] == doc_id
        assert c.get(f"/documents/{doc_id}", headers=ALICE).json()["status"] == "extracted"


def test_auth_off_refuses_everything(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'x.db'}", tmp_path / "s", dev_auth=False)
    with TestClient(app) as c:
        assert c.get("/review-queue", headers=ALICE).status_code == 501
