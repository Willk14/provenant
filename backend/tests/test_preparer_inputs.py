"""Values a contract does not state, such as the incremental borrowing rate, are preparer inputs (ADR 0005)."""
import io

from openpyxl import load_workbook

from tests.test_api import LEASE_LINES, PARTNER, PREPARER, review, uploaded_lease, who

BASIS = "Lessee's IBR: 5-year KES term borrowing quote from Rift Valley Bank, March 2026, adjusted for security"


def test_discount_rate_entered_with_its_basis_feeds_the_schedule(client, app):
    doc_id, fields = uploaded_lease(client, app, LEASE_LINES[:4])  # the lease does not state a rate
    assert "annual_discount_rate" not in fields

    def enter(body, headers=PREPARER):
        return client.post(f"/documents/{doc_id}/inputs", headers=headers, json=body)

    r = enter({"name": "annual_discount_rate", "value": "12%", "basis": "bank"})
    assert r.status_code == 422 and "basis" in r.json()["detail"]
    r = enter({"name": "monthly_payment", "value": "1,000.00", "basis": BASIS})
    assert r.status_code == 422 and "mark where the document states it" in r.json()["detail"]
    r = enter({"name": "annual_discount_rate", "value": "12", "basis": BASIS})
    assert r.status_code == 422 and "percentage" in r.json()["detail"]

    r = enter({"name": "annual_discount_rate", "value": "12%", "basis": BASIS})
    assert r.status_code == 201, r.text
    rate = r.json()
    assert (rate["page"], rate["basis"], rate["extractor"], rate["status"], rate["entered_by"]) == (
        0, BASIS, "preparer_input", "needs_review", "pat@firm-a")
    assert enter({"name": "annual_discount_rate", "value": "11%", "basis": BASIS}).status_code == 409

    # Judgement and maker-checker as for any rate: not the person who entered it, and a partner signs off
    assert review(client, rate["id"], who("pat@firm-a", "partner")).status_code == 403
    assert client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).status_code == 409
    assert review(client, rate["id"], PARTNER).status_code == 200
    assert review(client, fields["payment_timing"]["id"], PARTNER).status_code == 200
    sched = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER)
    assert sched.status_code == 200, sched.text

    book = load_workbook(io.BytesIO(client.get(f"/documents/{doc_id}/lease-schedule.xlsx", headers=PREPARER).content))
    rows = {r[1]: r for r in book["Provenance"].iter_rows(min_row=2, values_only=True)}
    assert rows["annual_discount_rate"][8] == "input" and rows["annual_discount_rate"][10] == BASIS
    assert client.get("/review-log/verify", headers=PREPARER).json()["intact"]


def test_a_rejected_input_can_be_replaced(client, app):
    doc_id, _ = uploaded_lease(client, app, LEASE_LINES[:4])
    first = client.post(f"/documents/{doc_id}/inputs", headers=PREPARER,
                        json={"name": "annual_discount_rate", "value": "30%", "basis": BASIS}).json()
    assert review(client, first["id"], PARTNER, "rejected").status_code == 200
    again = client.post(f"/documents/{doc_id}/inputs", headers=PREPARER,
                        json={"name": "annual_discount_rate", "value": "12%", "basis": BASIS})
    assert again.status_code == 201 and again.json()["id"] == first["id"] and again.json()["value"] == "12%"


def test_a_document_level_value_must_state_its_basis():
    import pytest

    from provenant.provenance import DocumentRef, ExtractedField, Status

    ref = DocumentRef("a" * 64)
    with pytest.raises(ValueError, match="basis"):
        ExtractedField("annual_discount_rate", "12%", 1.0, ref, "preparer_input", Status.NEEDS_REVIEW, basis=" ")
    field = ExtractedField("annual_discount_rate", "12%", 1.0, ref, "preparer_input", basis="bank quote, March 2026")
    assert field.is_input


def test_the_expansion_judgement_is_never_marked_on_the_pdf(client, app):
    from tests.test_amendments import lease_with_amendment

    _, amendment_id = lease_with_amendment(client, app)
    r = client.post(f"/documents/{amendment_id}/fields", headers=PREPARER, json={
        "name": "expansion_treatment", "value": "separate lease", "page": 1, "bbox": [0, 0, 600, 900]})
    assert r.status_code == 422 and "preparer's judgement" in r.json()["detail"]
    r = client.post(f"/documents/{amendment_id}/inputs", headers=PREPARER, json={
        "name": "expansion_treatment", "value": "a new lease", "basis": BASIS})
    assert r.status_code == 422 and "separate lease or modification" in r.json()["detail"]
