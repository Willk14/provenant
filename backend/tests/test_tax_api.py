import csv
import io
from decimal import Decimal

from tests.test_amendments import approve_pending, post
from tests.test_api import LEASE_LINES, OTHER_FIRM, PREPARER, make_pdf, run_jobs

D = Decimal


def tb_csv(rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["code", "name", "balance"])
    w.writerows([c, n, f"{b:.2f}"] for c, n, b in rows)
    return out.getvalue().encode()


ROWS = [
    ("4000", "Consulting fees earned", -1_000_000),
    ("5000", "Cost of sales", 400_000),
    ("5300", "KRA penalties", 10_000),
    ("5400", "Depreciation charge", 80_000),
    ("5600", "Interest on lease liabilities", 12_000),
    ("5700", "Sundry 7", 5_000),
    ("1500", "Plant and equipment - cost", 600_000),
    ("1510", "Accumulated depreciation - plant", -250_000),
    ("1000", "Equity Bank current account", 143_000),
    ("3000", "Share capital", -0),
]
ROWS[-1] = ("3000", "Share capital", -sum(b for *_, b in ROWS[:-1]))


def client_with_tb(client, year_end="2026-12-31", rows=ROWS):
    c = client.post("/clients", headers=PREPARER, json={"name": "Kilima Analytics Ltd"})
    cid = c.json()["id"] if c.status_code == 201 else client.get("/clients", headers=PREPARER).json()[0]["id"]
    r = client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": year_end},
                    files={"file": ("tb.csv", tb_csv(rows), "text/csv")})
    assert r.status_code == 201, r.text
    return cid


def test_mapping_suggestions_confirmation_and_computation(client, app):
    cid = client_with_tb(client)
    mapping = client.get(f"/clients/{cid}/years/2026-12-31/mapping", headers=PREPARER).json()
    accounts = {a["code"]: a for a in mapping["accounts"]}
    assert (accounts["5300"]["fs_line"], accounts["5300"]["tax_treatment"]) == ("operating_expenses", "disallowable")
    assert accounts["5600"]["tax_treatment"] == "lease_interest"
    assert accounts["5700"]["status"] == "unmapped"  # not guessed
    assert all(a["status"] in ("suggested", "unmapped") for a in accounts.values())

    blocked = client.get(f"/clients/{cid}/years/2026-12-31/tax", headers=PREPARER)
    assert blocked.status_code == 409 and "need a confirmed mapping" in blocked.json()["detail"]

    assert client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
                      json={"fs_line": "cash", "tax_treatment": "deductible"}).status_code == 422  # inconsistent
    assert client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
                      json={"fs_line": "operating_expenses", "tax_treatment": "deductible"}).status_code == 200
    confirmed = client.post(f"/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PREPARER).json()
    assert confirmed["confirmed"] == 9

    inputs = {"losses_brought_forward": "50,000", "allowances": [
        {"name": "Plant and machinery", "wdv_brought_forward": "300,000", "additions": "100,000",
         "rate_percent": "25", "method": "reducing", "basis": "Second Schedule (as entered by preparer)"}]}
    bad = {**inputs, "allowances": [{**inputs["allowances"][0], "basis": ""}]}
    assert client.put(f"/clients/{cid}/years/2026-12-31/inputs", headers=PREPARER, json=bad).status_code == 422
    assert client.put(f"/clients/{cid}/years/2026-12-31/inputs", headers=PREPARER, json=inputs).status_code == 200

    c = client.get(f"/clients/{cid}/years/2026-12-31/tax", headers=PREPARER).json()
    # PBT = 1,000,000 - (400,000 + 10,000 + 80,000 + 12,000 + 5,000) = 493,000
    # + 10,000 + 80,000 + 12,000 - 100,000 (25% of 400,000) = 495,000; - losses 50,000 = 445,000; tax 133,500
    assert D(c["profit_before_tax"]) == D(493_000)
    assert D(c["taxable_profit"]) == D(445_000) and D(c["tax_payable"]) == D(133_500)
    assert any("no leases are linked" in w for w in c["warnings"])
    penalty = next(a for a in c["adjustments"] if a["label"].startswith("Disallowable"))
    assert [c["accounts"][s] for s in penalty["sources"]] == ["5300 KRA penalties"]
    assert c["rules_version"] == "ke-2026.1"

    lead = {l["fs_line"]: D(l["amount"]) for l in client.get(
        f"/clients/{cid}/years/2026-12-31/mapping", headers=PREPARER).json()["lead_schedule"]}
    assert lead["revenue"] == D(1_000_000) and lead["operating_expenses"] == D(15_000)


def test_linked_lease_feeds_the_ifrs16_adjustments(client, app):
    cid = client_with_tb(client)
    client.post(f"/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PREPARER)
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    lease_id = post(client, make_pdf(LEASE_LINES), name="lease.pdf").json()["id"]
    run_jobs(app)
    approve_pending(client, lease_id)
    assert client.put(f"/documents/{lease_id}/client", headers=PREPARER, json={"client_id": cid}).status_code == 204

    c = client.get(f"/clients/{cid}/years/2026-12-31/tax", headers=PREPARER).json()
    (lease,) = c["leases"]
    # 12-month lease from 31 Jan 2026 at 1,000 in arrears: periods ending 28 Feb .. 31 Dec 2026 fall in the year.
    assert D(lease["payments"]) == D(11_000)
    assert D(lease["liability"]) == D("990.09")  # closing balance of period 11
    rentals = next(a for a in c["adjustments"] if a["label"].startswith("Lease rentals"))
    assert D(rentals["amount"]) == D(-11_000) and rentals["sources"] == [lease_id]
    assert not any("no leases are linked" in w for w in c["warnings"])
    assert any("lease interest per schedules" in w for w in c["warnings"])  # TB 12,000 vs schedule figure


def test_a_lease_starting_on_the_first_counts_its_december_period(client, app):
    # Periods run 1 Feb - 1 Mar, ..., 1 Dec - 1 Jan: the last one is December rent and belongs to 2026.
    cid = client_with_tb(client)
    client.post(f"/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PREPARER)
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    lines = [line.replace("31 January 2026", "1 February 2026") for line in LEASE_LINES]
    lease_id = post(client, make_pdf(lines), name="lease-1st.pdf").json()["id"]
    run_jobs(app)
    approve_pending(client, lease_id)
    client.put(f"/documents/{lease_id}/client", headers=PREPARER, json={"client_id": cid})
    (lease,) = client.get(f"/clients/{cid}/years/2026-12-31/tax", headers=PREPARER).json()["leases"]
    assert D(lease["payments"]) == D(11_000)  # February to December, 11 months


def test_confirmed_mappings_carry_forward_and_clients_are_isolated(client, app):
    cid = client_with_tb(client)
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "disallowable"})
    client_with_tb(client, year_end="2027-12-31")
    nxt = {a["code"]: a for a in client.get(f"/clients/{cid}/years/2027-12-31/mapping", headers=PREPARER).json()["accounts"]}
    assert (nxt["5700"]["status"], nxt["5700"]["tax_treatment"], nxt["5700"]["confirmed_by"]) == (
        "confirmed", "disallowable", "pat@firm-a")

    assert client.post("/clients", headers=PREPARER, json={"name": "Kilima Analytics Ltd"}).status_code == 409
    assert client.get("/clients", headers=OTHER_FIRM).json() == []
    assert client.get(f"/clients/{cid}/years/2026-12-31/mapping", headers=OTHER_FIRM).status_code == 404
    dup = client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": "2026-12-31"},
                      files={"file": ("tb.csv", tb_csv(ROWS), "text/csv")})
    assert dup.status_code == 409
    unbalanced = tb_csv([("1", "Bank", 10), ("2", "Revenue", -9)])
    r = client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": "2028-12-31"},
                    files={"file": ("tb.csv", unbalanced, "text/csv")})
    assert r.status_code == 422 and "does not balance" in r.json()["detail"]
