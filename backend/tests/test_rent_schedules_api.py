from decimal import Decimal

from tests.test_amendments import approve_pending, fields, post
from tests.test_api import PARTNER, PREPARER, make_pdf, review, run_jobs

LEASE = [
    "LEASE AGREEMENT",
    "Commencement Date: 31 January 2026",
    "Lease Term: 24 months",
    "Monthly Rent: KES 1,000.00 payable monthly in arrears",
    "From month 13 the monthly rent is KES 1,030.00. Months 1 and 2 are rent free.",
    "Discount Rate: 12%",
]


def enter(client, doc_id, name, value, box):
    r = client.post(
        f"/documents/{doc_id}/fields", headers=PREPARER, json={"name": name, "value": value, "page": 1, "bbox": box}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_lease_with_escalation_and_free_months_end_to_end(client, app):
    doc_id = post(client, make_pdf(LEASE), name="lease.pdf").json()["id"]
    run_jobs(app)
    approve_pending(client, doc_id)
    rent = fields(client, doc_id)["monthly_payment"]["bbox"]
    line = [0, rent[3] + 2, 600, rent[3] + 30]  # the escalation sentence, one line below the rent

    bad = client.post(f"/documents/{doc_id}/fields", headers=PREPARER,
                      json={"name": "rent_steps", "value": "month 13 1,030", "page": 1, "bbox": line})
    assert bad.status_code == 422
    steps_id = enter(client, doc_id, "rent_steps", "13: 1,030.00", line)
    free_id = enter(client, doc_id, "free_periods", "1, 2", line)

    # Unreviewed schedule terms block the schedule like any other term.
    assert client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).status_code == 409
    assert review(client, steps_id, PARTNER).status_code == 200
    assert review(client, free_id, PARTNER).status_code == 200

    sched = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).json()
    payments = [Decimal(p["payment"]) for p in sched["periods"]]
    assert payments == [0, 0] + [Decimal("1000")] * 10 + [Decimal("1030")] * 12
    assert Decimal(sched["periods"][-1]["closing"]) == 0
    assert sched["segments"][0]["inputs"]["rent_steps"] == steps_id

    # An amendment with a new rent replaces the lease's schedule from its effective date.
    amendment = ["FIRST AMENDMENT", "Effective Date: 31 July 2026", "Monthly Rent: KES 1,200.00", "Discount Rate: 18%"]
    amendment_id = post(client, make_pdf(amendment), amends=doc_id, name="amendment.pdf").json()["id"]
    run_jobs(app)
    approve_pending(client, amendment_id)
    sched = client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).json()
    payments = [Decimal(p["payment"]) for p in sched["periods"]]
    assert payments[6:] == [Decimal("1200")] * 18  # the lease's month-13 step no longer applies
    after = sched["segments"][1]["inputs"]
    assert "rent_steps" not in after and "free_periods" not in after
