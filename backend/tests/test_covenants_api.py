from datetime import date
from decimal import Decimal

from fpdf import FPDF

from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, review, run_jobs

AGREEMENT_LINES = [
    "FACILITY AGREEMENT",
    "22. Financial covenants",
    "(a) The Leverage Ratio in respect of any Relevant Period shall not exceed 3.50:1.",
    "(b) Interest Cover in respect of any Relevant Period shall not be less than 4.00:1.",
    "(c) Liquidity shall not at any time be less than KES 50,000,000.",
    "First Test Date: 31 December 2026",
    "The Borrower shall supply a Compliance Certificate within 45 days after the end of each Financial Quarter.",
    "Equity cure: a breach may be remedied within 20 days of the due date of that Compliance Certificate.",
    "Interest rate: 13.5% per annum.",
]


def agreement_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in AGREEMENT_LINES:
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def tb_csv(*, revenue, opex, interest, cash, borrowings, leases=0) -> bytes:
    """Quarter in millions, debit-positive balances, closed by an 'other' line."""
    m = 1_000_000
    lines = [
        ("4000", "Revenue", "revenue", -revenue * m),
        ("5000", "Operating expenses", "operating_expenses", opex * m),
        ("6100", "Interest payable", "interest_expense", interest * m),
        ("1000", "Bank", "cash", cash * m),
        ("2500", "Term loan", "borrowings", -borrowings * m),
        ("2600", "Lease liabilities", "lease_liabilities", -leases * m),
    ]
    lines.append(("3000", "Equity and other", "other", -sum(b for *_, b in lines)))
    return ("code,name,category,balance\n" + "".join(f"{c},{n},{k},{b:.2f}\n" for c, n, k, b in lines)).encode()


STEADY = dict(revenue=300, opex=225, interest=18, cash=120, borrowings=900, leases=60)
WEAK = dict(revenue=260, opex=235, interest=20, cash=60, borrowings=950, leases=58)
QUARTERS = ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]


def upload_agreement(client, app) -> str:
    r = client.post(
        "/documents",
        headers=PREPARER,
        data={"kind": "credit_agreement"},
        files={"file": ("facility.pdf", agreement_pdf(), "application/pdf")},
    )
    assert r.status_code == 201 and r.json()["kind"] == "credit_agreement"
    run_jobs(app)
    return r.json()["id"]


def upload_tb(client, doc_id, period_end, body: bytes, headers=PREPARER):
    return client.post(
        f"/documents/{doc_id}/trial-balances",
        headers=headers,
        data={"period_end": period_end},
        files={"file": (f"tb-{period_end}.csv", body, "text/csv")},
    )


def verify_all(client, doc_id):
    for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]:
        if f["status"] == "needs_review":
            assert review(client, f["id"], PARTNER).status_code == 200


def test_covenant_terms_are_extracted_and_lease_terms_are_not(client, app):
    doc_id = upload_agreement(client, app)
    fields = {f["name"]: f for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}
    assert {n: f["value"] for n, f in fields.items()} == {
        "max_leverage_ratio": "3.50x",
        "min_interest_cover": "4.00x",
        "min_liquidity": "50,000,000",
        "first_test_date": "2026-12-31",
        "reporting_days": "45",
        "cure_period_days": "20",
    }  # "Interest rate: 13.5%" is not a covenant term
    assert fields["max_leverage_ratio"]["status"] == "needs_review"  # thresholds are a partner's call
    assert review(client, fields["max_leverage_ratio"]["id"], REVIEWER).status_code == 403
    assert client.get(f"/documents/{doc_id}/covenants", headers=PREPARER).status_code == 409  # not verified yet
    assert client.get(f"/documents/{doc_id}/lease-schedule", headers=PREPARER).status_code == 409


def test_trial_balance_validation(client, app):
    doc_id = upload_agreement(client, app)
    good = tb_csv(**STEADY)
    assert upload_tb(client, doc_id, "2026-03-31", good).status_code == 201
    assert upload_tb(client, doc_id, "2026-03-31", good).status_code == 409  # one per period
    unbalanced = good.replace(b"Bank,cash,120000000.00", b"Bank,cash,120000001.00")
    r = upload_tb(client, doc_id, "2026-06-30", unbalanced)
    assert r.status_code == 422 and "does not balance" in r.json()["detail"]
    r = upload_tb(client, doc_id, "2026-06-30", good.replace(b",cash,", b",cashish,"))
    assert r.status_code == 422 and "category" in r.json()["detail"]
    assert upload_tb(client, doc_id, "2026-06-15", good).status_code == 422  # not a month-end
    assert upload_tb(client, doc_id, "2026-06-30", b"code,name\n1,x\n").status_code == 422
    assert upload_tb(client, doc_id, "2026-06-30", good, headers=OTHER_FIRM).status_code == 404

    listed = client.get(f"/documents/{doc_id}/trial-balances", headers=PREPARER).json()
    assert [(t["period_end"], t["lines"]) for t in listed] == [("2026-03-31", 7)]
    assert client.delete(f"/trial-balances/{listed[0]['id']}", headers=REVIEWER).status_code == 403  # not theirs
    assert client.delete(f"/trial-balances/{listed[0]['id']}", headers=PREPARER).status_code == 204
    assert client.get(f"/documents/{doc_id}/trial-balances", headers=PREPARER).json() == []


def test_covenant_run_results_deadlines_and_alerts(client, app):
    doc_id = upload_agreement(client, app)
    verify_all(client, doc_id)
    for q in QUARTERS:
        assert upload_tb(client, doc_id, q, tb_csv(**STEADY)).status_code == 201
    assert upload_tb(client, doc_id, "2027-03-31", tb_csv(**WEAK)).status_code == 201

    r = client.get(f"/documents/{doc_id}/covenants", headers=PREPARER, params={"as_of": "2027-05-20"}).json()
    by_date = {t["period_end"]: t for t in r["test_dates"]}
    december, march, june = by_date["2026-12-31"], by_date["2027-03-31"], by_date["2027-06-30"]

    results = {x["kind"]: x for x in december["results"]}
    assert Decimal(results["leverage"]["value"]) == Decimal("2.8") and results["leverage"]["status"] == "pass"
    assert results["interest_cover"]["status"] == "warning"  # 4.17x against 4.00x
    assert Decimal(results["min_liquidity"]["value"]) == Decimal("120000000")
    assert len(december["trial_balance_ids"]) == 4 and december["certificate_due"] == "2027-02-14"

    statuses = {x["kind"]: x["status"] for x in march["results"]}
    assert statuses == {"leverage": "breach", "interest_cover": "breach", "min_liquidity": "pass"}
    assert march["cure_deadline"] == "2027-06-04"
    lev = next(x for x in march["results"] if x["kind"] == "leverage")
    assert Decimal(lev["value"]) == round(Decimal(948) / Decimal(250), 4)
    assert [w["label"] for w in lev["workings"]][3] == "Net debt"
    fields = {f["name"]: f["id"] for f in r["inputs"]}
    assert lev["field_id"] == fields["max_leverage_ratio"]  # each result points at its clause

    assert june["status"] == "awaiting" and "2027-06-30" in june["missing"]
    assert [a["severity"] for a in r["alerts"]][:2] == ["breach", "warning"]
    assert "cure window closes 2027-06-04" in r["alerts"][0]["message"]

    # After the June certificate deadline with no June trial balance, that quarter is overdue.
    later = client.get(f"/documents/{doc_id}/covenants", headers=PREPARER, params={"as_of": "2027-08-20"}).json()
    assert any(a["severity"] == "overdue" and "2027-06-30" in a["message"] for a in later["alerts"])
    assert not any("cure window" in a["message"] for a in later["alerts"])  # the March cure window has closed


def test_rejecting_a_proposed_covenant_drops_it(client, app):
    doc_id = upload_agreement(client, app)
    fields = {f["name"]: f for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}
    assert review(client, fields["min_liquidity"]["id"], PARTNER, "rejected").status_code == 200
    verify_all(client, doc_id)
    r = client.get(f"/documents/{doc_id}/covenants", headers=PREPARER, params={"as_of": date(2026, 11, 17).isoformat()})
    assert r.status_code == 200
    kinds = {x["kind"] for t in r.json()["test_dates"] for x in t["results"]}
    assert "min_liquidity" not in kinds
