from datetime import date
from decimal import Decimal

from fpdf import FPDF

from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, review, run_jobs, uploaded_lease

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


def agreement_pdf(lines=tuple(AGREEMENT_LINES)) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in lines:
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


def upload_agreement(client, app, lines=tuple(AGREEMENT_LINES)) -> str:
    r = client.post(
        "/documents",
        headers=PREPARER,
        data={"kind": "credit_agreement"},
        files={"file": ("facility.pdf", agreement_pdf(lines), "application/pdf")},
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


def test_relative_first_test_date_and_threshold_step(client, app):
    relative = "Testing begins with the first full Financial Quarter after the Closing Date (1 November 2026)."
    doc_id = upload_agreement(client, app, [relative if x.startswith("First Test") else x for x in AGREEMENT_LINES])
    fields = client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]
    assert "first_test_date" not in {f["name"] for f in fields}  # a relative date is not read as a date

    def mark(name, value):
        return client.post(f"/documents/{doc_id}/fields", headers=PREPARER,
                           json={"name": name, "value": value, "page": 1, "bbox": [0, 0, 600, 900]})

    assert mark("first_test_date", "first full quarter after closing").status_code == 422
    assert mark("max_leverage_steps", "2027-06-30: 3.25").status_code == 422  # a ratio must carry "x"
    # Q4 2026 began 1 Oct, before 1 Nov, so the first full quarter is January to March 2027.
    assert mark("first_test_date", "first full quarter after 2026-11-01, year end December").status_code == 201
    step = mark("max_leverage_steps", "2027-06-30: 3.25x")
    assert step.status_code == 201 and step.json()["status"] == "needs_review"
    assert review(client, step.json()["id"], REVIEWER).status_code == 403  # a threshold is a partner's call
    assert client.get(f"/documents/{doc_id}/covenants", headers=PREPARER).status_code == 409  # not verified yet
    verify_all(client, doc_id)

    for q in QUARTERS[1:]:
        upload_tb(client, doc_id, q, tb_csv(**STEADY))
    upload_tb(client, doc_id, "2027-03-31", tb_csv(**WEAK))
    upload_tb(client, doc_id, "2027-06-30", tb_csv(**STEADY))
    r = client.get(f"/documents/{doc_id}/covenants", headers=PREPARER, params={"as_of": "2027-07-01"}).json()
    assert r["terms"]["first_test_date"] == "2027-03-31" and r["test_dates"][0]["period_end"] == "2027-03-31"

    ids = {f["name"]: f["id"] for f in r["inputs"]}
    march, june = (next(x for x in t["results"] if x["kind"] == "leverage") for t in r["test_dates"][:2])
    assert Decimal(march["threshold"]) == Decimal("3.50") and march["field_id"] == ids["max_leverage_ratio"]
    # June: net debt 900 + 60 - 120 = 840; LTM EBITDA 75 + 75 + 25 + 75 = 250; 840 / 250 = 3.36x.
    # Within the base 3.50x, but above the 3.25x that applies from June.
    assert Decimal(june["value"]) == Decimal("3.36") and june["status"] == "breach"
    assert Decimal(june["threshold"]) == Decimal("3.25") and june["field_id"] == ids["max_leverage_steps"]
    assert june["note"] == "Threshold 3.25x applies from 2027-06-30"


def _enter_input(client, doc_id, name, value):
    r = client.post(f"/documents/{doc_id}/inputs", headers=PREPARER,
                    json={"name": name, "value": value, "basis": "Facility agreement, definitions clause 1.1"})
    assert r.status_code == 201, r.text


def _leverage(client, doc_id, period):
    run = client.get(f"/documents/{doc_id}/covenants", headers=PREPARER, params={"as_of": "2027-05-20"}).json()
    quarter = next(t for t in run["test_dates"] if t["period_end"] == period)
    return next(x for x in quarter["results"] if x["kind"] == "leverage")


def test_addback_is_approved_by_a_partner_before_it_counts_and_is_capped(client, app):
    doc_id = upload_agreement(client, app)
    _enter_input(client, doc_id, "addback_cap_percent", "5%")
    _enter_input(client, doc_id, "addback_cap_basis", "before add-backs")
    url = f"/documents/{doc_id}/covenants/adjustments"
    body = {"kind": "addback", "quarter_end": "2027-03-31", "label": "Restructuring costs", "amount": "30,000,000",
            "capped": True, "clause": "Clause 1.1(c)", "basis": "Board paper of 14 March 2027"}
    assert client.post(url, headers=PREPARER, json=body).status_code == 409  # terms are not verified yet
    verify_all(client, doc_id)
    for q in QUARTERS:
        upload_tb(client, doc_id, q, tb_csv(**STEADY))
    upload_tb(client, doc_id, "2027-03-31", tb_csv(**WEAK))

    assert client.post(url, headers=PREPARER, json={**body, "quarter_end": "2027-02-28"}).status_code == 422
    assert client.post(url, headers=PREPARER, json={**body, "amount": "-5"}).status_code == 422  # a capped deduction
    assert client.post(url, headers=PREPARER, json={**body, "basis": "short"}).status_code == 422
    assert client.post(url, headers=OTHER_FIRM, json=body).status_code == 404
    item = client.post(url, headers=PREPARER, json=body)
    assert item.status_code == 201
    approve = f"{url}/{item.json()['id']}/approve"

    # Net debt 950 + 58 - 60 = 948. LTM EBITDA 75 + 75 + 75 + 25 = 250, so 3.79x: unapproved, it does not count.
    assert _leverage(client, doc_id, "2027-03-31")["status"] == "breach"
    assert Decimal(_leverage(client, doc_id, "2027-03-31")["value"]) == round(Decimal(948) / Decimal(250), 4)
    assert client.post(approve, headers=PREPARER).status_code == 403
    assert client.post(approve, headers=REVIEWER).status_code == 403
    assert client.post(approve, headers=OTHER_FIRM).status_code == 404
    assert client.post(approve, headers=PARTNER).status_code == 200

    # The cap is 5% of 250 = 12.5 of the 30, so EBITDA is 262.5 and leverage 948 / 262.5 = 3.6114x: still a breach.
    capped = _leverage(client, doc_id, "2027-03-31")
    assert Decimal(capped["value"]) == round(Decimal(948) / Decimal("262.5"), 4) and capped["status"] == "breach"
    lines = {w["label"]: w for w in capped["workings"]}
    assert lines["Add-back: Restructuring costs (2027-03-31)"]["source"] == item.json()["id"]
    assert Decimal(lines["Add-backs above the cap"]["amount"]) == Decimal("-17500000")

    assert client.delete(f"{url}/{item.json()['id']}", headers=PREPARER).status_code == 403  # approved
    assert client.delete(f"{url}/{item.json()['id']}", headers=PARTNER).status_code == 204
    assert Decimal(_leverage(client, doc_id, "2027-03-31")["value"]) == round(Decimal(948) / Decimal(250), 4)


def test_frozen_gaap_lease_uses_its_verified_schedule(client, app):
    lease_id, _ = uploaded_lease(client, app)
    verify_all(client, lease_id)
    doc_id = upload_agreement(client, app)
    _enter_input(client, doc_id, "lease_treatment", "frozen GAAP")
    verify_all(client, doc_id)
    for q in QUARTERS:
        upload_tb(client, doc_id, q, tb_csv(**STEADY))
    assert _leverage(client, doc_id, "2026-12-31")["status"] == "undetermined"  # no lease approved yet

    url = f"/documents/{doc_id}/covenants/adjustments"
    body = {"kind": "frozen_lease", "lease_document_id": lease_id, "clause": "Clause 1.3", "basis": "Operating lease before IFRS 16"}
    assert client.post(url, headers=PREPARER, json={**body, "lease_document_id": doc_id}).status_code == 422
    item = client.post(url, headers=PREPARER, json=body)
    assert item.status_code == 201
    assert client.post(url, headers=PREPARER, json=body).status_code == 409  # once per agreement
    assert client.post(f"{url}/{item.json()['id']}/approve", headers=PARTNER).status_code == 200

    result = _leverage(client, doc_id, "2026-12-31")
    charge = next(w for w in result["workings"] if w["label"].startswith("Operating lease charge"))
    # 12 payments of 1,000 spread evenly is 1,000 a month. The lease's months ending in 2026 are counted.
    months = int(-Decimal(charge["amount"]) / 1000)
    assert charge["source"] == lease_id and months == 11
    assert result["status"] in ("pass", "warning")


def test_springing_and_delivery_inputs_are_confirmed_before_they_count(client, app):
    doc_id = upload_agreement(client, app)
    for name, value in (("springing_usage_percent", "35%"), ("revolver_commitment", "100,000,000"),
                        ("cure_day_basis", "business"), ("cure_runs_from", "delivery")):
        r = client.post(f"/documents/{doc_id}/inputs", headers=PREPARER,
                        json={"name": name, "value": value, "basis": "Facility agreement, clause 22.3 as read"})
        assert r.status_code == 201, r.text
    verify_all(client, doc_id)
    for q in QUARTERS:
        upload_tb(client, doc_id, q, tb_csv(**STEADY))
    upload_tb(client, doc_id, "2027-03-31", tb_csv(**WEAK))
    url = f"/documents/{doc_id}/covenants"
    period = f"{url}/periods/2027-03-31"

    def march():
        run = client.get(url, headers=PREPARER, params={"as_of": "2027-05-20"}).json()
        return run, next(t for t in run["test_dates"] if t["period_end"] == "2027-03-31")

    _, q = march()
    assert next(x for x in q["results"] if x["kind"] == "leverage")["status"] == "undetermined"

    body = {"revolver_drawn": "60,000,000", "delivered_on": "2027-05-12", "basis": "Agent's statement of 31 March"}
    assert client.put(period, headers=PREPARER, json={**body, "basis": "short"}).status_code == 422
    assert client.put(period, headers=PREPARER, json={**body, "delivered_on": "2027-03-01"}).status_code == 422
    assert client.put(period, headers=OTHER_FIRM, json=body).status_code == 404
    assert client.put(period, headers=PREPARER, json=body).status_code == 200
    _, q = march()  # entered but unconfirmed: still not used
    assert next(x for x in q["results"] if x["kind"] == "leverage")["status"] == "undetermined"
    assert client.post(f"{period}/confirm", headers=PREPARER).status_code == 403
    assert client.post(f"{period}/confirm", headers=REVIEWER).status_code == 200

    run, q = march()
    assert next(x for x in q["results"] if x["kind"] == "leverage")["status"] == "breach"
    # 20 business days from Wednesday 12 May 2027: 13-14 (2), then three full weeks to 4 June (17),
    # 7-9 June (20), so Wednesday 9 June.
    assert q["cure_deadline"] == "2027-06-09" and q["cure_pending"] is False
    assert run["terms"]["cure_day_basis"] == "business" and run["period_inputs"][0]["confirmed_by"]

    # Changing a confirmed figure needs confirming again.
    assert client.put(period, headers=PREPARER, json={**body, "revolver_drawn": "10,000,000"}).status_code == 200
    _, q = march()
    assert next(x for x in q["results"] if x["kind"] == "leverage")["status"] == "undetermined"
