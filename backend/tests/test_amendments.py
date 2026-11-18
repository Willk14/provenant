import io
from decimal import Decimal

from openpyxl import load_workbook

from tests.test_api import LEASE_LINES, OTHER_FIRM, PARTNER, PREPARER, REVIEWER, make_pdf, review, run_jobs, who
from tests.test_modifications import annuity

AMENDMENT_LINES = [
    "FIRST AMENDMENT TO LEASE",
    "Effective Date: 31 July 2026",
    "Monthly Rent: KES 1,200.00 payable monthly in arrears",
    "Discount Rate: 18%",
]


def post(client, data: bytes, amends: str | None = None, headers=PREPARER, name="doc.pdf"):
    form = {"amends": amends} if amends else None
    return client.post("/documents", headers=headers, data=form, files={"file": (name, data, "application/pdf")})


def lease_with_amendment(client, app, amendment_lines=AMENDMENT_LINES):
    lease = post(client, make_pdf(LEASE_LINES), name="lease.pdf").json()
    amendment = post(client, make_pdf(amendment_lines), amends=lease["id"], name="amendment-1.pdf").json()
    run_jobs(app)
    return lease["id"], amendment["id"]


def fields(client, doc_id):
    return {f["name"]: f for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}


def approve_pending(client, doc_id):
    for f in fields(client, doc_id).values():
        if f["status"] == "needs_review":
            assert review(client, f["id"], PARTNER).status_code == 200


def test_amendment_links_to_its_lease_by_id_and_hash(client, app):
    lease_id, amendment_id = lease_with_amendment(client, app)
    lease = client.get(f"/documents/{lease_id}", headers=PREPARER).json()
    amendment = client.get(f"/documents/{amendment_id}", headers=PREPARER).json()
    assert lease["kind"] == "lease" and [a["id"] for a in lease["amendments"]] == [amendment_id]
    assert amendment["kind"] == "amendment"
    assert (amendment["parent_id"], amendment["parent_sha256"]) == (lease_id, lease["sha256"])
    assert amendment["parent"]["filename"] == "lease.pdf"
    # Only amendment terms are kept: the "payable ... in arrears" wording is not a term of an amendment.
    got = fields(client, amendment_id)
    assert set(got) == {"effective_date", "monthly_payment", "annual_discount_rate"}
    assert got["effective_date"]["value"] == "2026-07-31" and got["effective_date"]["status"] == "needs_review"
    listed = {d["id"]: d for d in client.get("/documents", headers=PREPARER).json()}
    assert listed[amendment_id]["parent_id"] == lease_id and listed[amendment_id]["missing_terms"] == 0


def test_schedule_waits_for_the_amendment_then_remeasures(client, app):
    lease_id, amendment_id = lease_with_amendment(client, app)
    approve_pending(client, lease_id)
    blocked = client.get(f"/documents/{lease_id}/lease-schedule", headers=PREPARER)
    assert blocked.status_code == 409 and "amendment-1.pdf" in blocked.json()["detail"]

    # When it takes effect is a judgement call: reviewers cannot sign it off, partners can.
    effective = fields(client, amendment_id)["effective_date"]["id"]
    assert review(client, effective, REVIEWER).status_code == 403
    approve_pending(client, amendment_id)

    sched = client.get(f"/documents/{lease_id}/lease-schedule", headers=PREPARER).json()
    (r,) = sched["remeasurements"]
    assert r["after_period"] == 6 and r["document_id"] == amendment_id and r["filename"] == "amendment-1.pdf"
    assert Decimal(r["liability_after"]) == annuity("1200", 6, "0.015")
    assert [p["segment"] for p in sched["periods"]] == [0] * 6 + [1] * 6
    assert Decimal(sched["periods"][-1]["closing"]) == 0

    lease_fields, amendment_fields = fields(client, lease_id), fields(client, amendment_id)
    base, after = sched["segments"]
    assert (base["document_id"], base["from_period"]) == (lease_id, 1)
    assert (after["document_id"], after["from_period"]) == (amendment_id, 7)
    assert after["inputs"]["monthly_payment"] == amendment_fields["monthly_payment"]["id"]
    assert after["inputs"]["term_months"] == lease_fields["term_months"]["id"]  # unchanged terms carry over
    assert len(sched["chain_fingerprint"]) == 64
    assert {f["document_id"] for f in sched["inputs"]} == {lease_id, amendment_id}

    wb = load_workbook(io.BytesIO(client.get(f"/documents/{lease_id}/lease-schedule.xlsx", headers=PREPARER).content))
    chain = list(wb["Chain"].iter_rows(min_row=4, values_only=True))
    assert [row[1] for row in chain] == ["lease.pdf", "amendment-1.pdf"]
    assert wb["Chain"]["B1"].value == sched["chain_fingerprint"]
    assert wb["Schedule"].cell(row=11, column=8).value == "amendment-1.pdf"  # period 7 is under the amendment


def test_two_amendments_on_the_same_date_are_refused(client, app):
    lease_id, _ = lease_with_amendment(client, app)
    second = AMENDMENT_LINES[:2] + ["Monthly Rent: KES 1,300.00", "Discount Rate: 19%"]
    second_id = post(client, make_pdf(second), amends=lease_id, name="side-letter.pdf").json()["id"]
    run_jobs(app)
    for doc_id in (lease_id, *[a["id"] for a in client.get(f"/documents/{lease_id}", headers=PREPARER).json()["amendments"]]):
        approve_pending(client, doc_id)
    r = client.get(f"/documents/{lease_id}/lease-schedule", headers=PREPARER)
    assert r.status_code == 409 and "ambiguous" in r.json()["detail"]
    assert second_id


def test_chain_rules(client, app):
    lease_id, amendment_id = lease_with_amendment(client, app)
    lease_pdf = make_pdf(LEASE_LINES)
    standalone = post(client, lease_pdf).json()
    # The same file cannot be both a lease and an amendment.
    assert post(client, lease_pdf, amends=lease_id).status_code == 409
    # Amendments chain to the lease, not to other amendments.
    assert post(client, make_pdf(["x"]), amends=amendment_id).status_code == 422
    # Other firms cannot attach to this lease.
    assert post(client, make_pdf(["y"]), amends=lease_id, headers=OTHER_FIRM).status_code == 404
    # An amendment has no payment timing of its own; a lease has no effective date.
    body = {"name": "payment_timing", "value": "advance", "page": 1, "bbox": [0, 0, 600, 900]}
    assert client.post(f"/documents/{amendment_id}/fields", headers=PREPARER, json=body).status_code == 422
    body = {"name": "effective_date", "value": "2026-07-31", "page": 1, "bbox": [0, 0, 600, 900]}
    assert client.post(f"/documents/{standalone['id']}/fields", headers=PREPARER, json=body).status_code == 422
    # The amendment's own schedule is the lease's.
    assert client.get(f"/documents/{amendment_id}/lease-schedule", headers=PREPARER).status_code == 409


def test_off_boundary_effective_date_blocks_with_an_explanation(client, app):
    lines = ["AMENDMENT", "Effective Date: 15 July 2026", "Monthly Rent: KES 1,200.00", "Discount Rate: 18%"]
    lease_id, amendment_id = lease_with_amendment(client, app, lines)
    approve_pending(client, lease_id)
    approve_pending(client, amendment_id)
    r = client.get(f"/documents/{lease_id}/lease-schedule", headers=who("x", "preparer"))
    assert r.status_code == 409 and "period boundary" in r.json()["detail"]


def test_extension_signed_mid_term_reads_its_three_dates_and_remeasures(client, app):
    """The Kura pattern end to end: agreed 15 July, extension and new rent from the end of the original term."""
    lines = [
        "SECOND AMENDMENT TO LEASE",
        "Effective Date: 15 July 2026",
        "Extension Commencement Date: 31 January 2027",
        "New Expiry Date: 30 April 2027",
        "Monthly Rent: KES 1,100.00 payable monthly in arrears",
        "Discount Rate: 18%",
    ]
    lease_id, amendment_id = lease_with_amendment(client, app, lines)
    got = {name: f["value"] for name, f in fields(client, amendment_id).items()}
    assert (got["effective_date"], got["terms_start_date"], got["expiry_date"]) == (
        "2026-07-15", "2027-01-31", "2027-04-30")
    approve_pending(client, lease_id)
    approve_pending(client, amendment_id)

    s = client.get(f"/documents/{lease_id}/lease-schedule", headers=PREPARER)
    assert s.status_code == 200, s.text
    s = s.json()
    (r,) = s["remeasurements"]
    # Worked in test_modifications: remeasured after period 6 (31 Jul 2026) to 8,626.85
    assert (r["effective"], r["remeasured_on"], r["terms_start"], r["after_period"]) == (
        "2026-07-15", "2026-07-31", "2027-01-31", 6)
    assert Decimal(r["liability_after"]) == Decimal("8626.85")
    assert [Decimal(p["payment"]) for p in s["periods"]] == [Decimal(1000)] * 12 + [Decimal(1100)] * 3
