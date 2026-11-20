"""M-Pesa and bank statements through the API."""
from decimal import Decimal

from tests.test_api import OTHER_FIRM, PREPARER
from tests.test_etims_api import setup
from tests.test_statements import BANK, MPESA

D = Decimal


def post(client, year, data, kind, label, name="statement.csv"):
    return client.post(f"{year}/statements", headers=PREPARER, data={"kind": kind, "label": label},
                       files={"file": (name, data, "text/csv")})


def test_upload_consolidate_categorise_and_compare_with_revenue(client, app):
    _, year = setup(client)
    assert post(client, year, MPESA, "mpesa", "M-Pesa Till 123456", "mpesa.csv").status_code == 201
    r = post(client, year, BANK, "bank", "Equity Bank 0123", "equity.csv")
    assert r.status_code == 201, r.text
    out = r.json()
    assert [(x["label"], x["movements"], D(x["money_in"]), D(x["money_out"])) for x in out["statements"]] == [
        ("M-Pesa Till 123456", 4, D(100_000), D("52030.00")),  # 30 + 2,000 + 50,000 out
        ("Equity Bank 0123", 5, D(50_000), D(190_500)),  # 40,000 + 150,000 + 500 out
    ]
    assert (out["duplicates"], out["transfers"]) == (0, 1)
    assert [(b["source"], b["row"], D(b["gap"])) for b in out["breaks"]] == [("Equity Bank 0123", 5, D(-1_000))]
    assert D(out["revenue"]) == D(1_000_000) and D(out["confirmed_sales_receipts"]) == 0

    assert post(client, year, MPESA, "mpesa", "M-Pesa again").status_code == 409  # same file twice
    receipt = next(t for t in out["transactions"] if t["reference"] == "SC1A1")
    assert (receipt["category"], receipt["status"]) == ("sales_receipt", "suggested")

    r = client.post(f"{year}/statements/confirm-suggestions", headers=PREPARER).json()
    assert all(t["status"] == "confirmed" for t in r["transactions"])
    assert D(r["confirmed_sales_receipts"]) == D(100_000)
    fuel = next(t for t in r["transactions"] if t["reference"] == "SC4X4")
    r = client.put(f"{year}/statements/categories", headers=PREPARER,
                   json={"keys": [fuel["key"]], "category": "owner"}).json()
    assert next(t for t in r["transactions"] if t["reference"] == "SC4X4")["category"] == "owner"
    bad = client.put(f"{year}/statements/categories", headers=PREPARER, json={"keys": [fuel["key"]], "category": "x"})
    assert bad.status_code == 422

    assert client.get(f"{year}/statements", headers=OTHER_FIRM).status_code == 404
    first = out["statements"][0]["id"]
    after = client.delete(f"{year}/statements/{first}", headers=PREPARER).json()
    assert [x["label"] for x in after["statements"]] == ["Equity Bank 0123"] and after["transfers"] == 0
