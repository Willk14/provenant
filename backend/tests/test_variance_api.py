"""Variance checks through the API, against the mapped trial balance and the purchase ledger."""
from decimal import Decimal

from tests.test_api import OTHER_FIRM, PREPARER
from tests.test_etims_api import CLIENT_LEDGER, setup, upload

D = Decimal
# 12 monthly VAT returns of 82,500 = 990,000 declared turnover
VAT = "period,standard_rated_sales,zero_rated_sales,exempt_sales\n" + "".join(
    f"2026-{m:02d},82500,0,0\n" for m in range(1, 13))


def test_checks_compare_books_with_returns_and_take_explanations(client, app):
    _, year = setup(client)
    checks = {c["key"]: c for c in client.get(f"{year}/checks", headers=PREPARER).json()}
    assert checks["vat_turnover"]["book"] is None  # nothing until the return is uploaded

    r = upload(client, f"{year}/checks/returns/vat", VAT.encode(), "vat.csv")
    assert r.status_code == 201, r.text
    vat = {c["key"]: c for c in r.json()}["vat_turnover"]
    # Revenue 1,000,000 (account 4000) - 990,000 declared = 10,000; tolerance max(1,000, 0.5% = 5,000) = 5,000
    assert (D(vat["book"]), D(vat["declared"]), D(vat["variance"]), D(vat["tolerance"])) == (
        D(1_000_000), D(990_000), D(10_000), D(5_000))
    assert vat["needs_explanation"] and vat["book_accounts"] == ["4000"] and vat["notes"] == []

    r = client.put(f"{year}/checks/vat_turnover/explanation", headers=PREPARER, json={"text": "short"})
    assert r.status_code == 422
    r = client.put(f"{year}/checks/vat_turnover/explanation", headers=PREPARER,
                   json={"text": "December invoice of 10,000 declared in the January 2027 VAT return"})
    vat = {c["key"]: c for c in r.json()}["vat_turnover"]
    assert not vat["needs_explanation"] and vat["explained_by"] == "pat@firm-a"

    paye = {c["key"]: c for c in upload(client, f"{year}/checks/returns/paye",
                                        b"period,gross_pay,paye\n2026-01,50000,8000\n", "p10.csv").json()}["paye"]
    # No employee cost accounts in this trial balance: 0 - 50,000; and 11 of 12 returns missing
    assert D(paye["variance"]) == D(-50_000) and paye["needs_explanation"]
    assert paye["notes"][0].startswith("11 of 12 PAYE returns missing")

    certs = (b"certificate_number,withholdee_pin,withholdee_name,date,gross_amount,wht_amount\n"
             b"W-1,P053333333C,Rift Fuel,2026-03-31,5000,250\n")
    wht = {c["key"]: c for c in upload(client, f"{year}/checks/returns/wht", certs, "wht.csv").json()}["wht"]
    assert wht["book"] is None and "purchase ledger" in wht["notes"][0]
    upload(client, f"{year}/etims/ledger", CLIENT_LEDGER, "purchases.csv")
    wht = {c["key"]: c for c in client.get(f"{year}/checks", headers=PREPARER).json()}["wht"]
    assert [(x["label"], D(x["book"]), D(x["declared"])) for x in wht["rows"]] == [
        ("Rift Fuel (P053333333C)", D(5_000), D(5_000))]
    assert not wht["needs_explanation"]

    assert client.get(f"{year}/checks", headers=OTHER_FIRM).status_code == 404
    bad = upload(client, f"{year}/checks/returns/vat", b"period,standard_rated_sales,zero_rated_sales,exempt_sales\n2026-13,1,0,0\n", "x.csv")
    assert bad.status_code == 422 and "period" in bad.json()["detail"]
