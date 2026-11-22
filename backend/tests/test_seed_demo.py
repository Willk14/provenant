"""The demo seed's tax records must compute: a rule change that breaks the demo should fail here, not on screen."""
import importlib.util
from decimal import Decimal
from pathlib import Path

from tests.test_api import PREPARER

D = Decimal
SEED = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"


def load_seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kilima_demo_computes_from_its_seeded_records(client, app):
    seed = load_seed()
    cid = client.post("/clients", headers=PREPARER, json={"name": "Kilima Analytics Ltd"}).json()["id"]
    year = f"/clients/{cid}/years/2026-12-31"
    r = client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": "2026-12-31"},
                    files={"file": ("tb.csv", seed.client_tb_csv(), "text/csv")})
    assert r.status_code == 201, r.text
    client.post(f"{year}/confirm-suggestions", headers=PREPARER)
    client.put(f"/clients/{cid}/mappings/5250", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "disallowable"})
    r = client.post(f"{year}/fixed-assets", headers=PREPARER,
                    files={"file": ("far.csv", seed.FIXED_ASSET_REGISTER, "text/csv")})
    assert r.status_code == 201, r.text
    assert client.put(f"{year}/inputs", headers=PREPARER, json=seed.TAX_INPUTS).status_code == 200

    r = client.get(f"{year}/tax", headers=PREPARER)
    assert r.status_code == 200, r.text
    c = r.json()
    # PBT 221,630,000 + disallowable 5,090,000 + depreciation 17,750,000 + lease interest 1,330,000
    # - exempt dividends 2,400,000 - capital deduction 25% x (opening 30,000,000 + additions 8,000,000) = 9,500,000
    # - disposal loss on IT-2022-03 (residual 800,000 - 200,000 = 600,000, sold for 300,000) = 300,000
    # = 233,600,000; less losses 40,000,000 = 193,600,000; tax at 30% = 58,080,000
    assert D(c["profit_before_tax"]) == D(221_630_000)
    assert D(c["taxable_profit"]) == D(193_600_000) and D(c["tax_payable"]) == D(58_080_000)
    loss = next(a for a in c["adjustments"] if a["label"].startswith("Disposal loss"))
    assert D(loss["amount"]) == D(-300_000) and loss["sources"] == ["asset:IT-2022-03"]


def test_working_papers_pack_holds_every_result_with_its_source(client, app):
    """One workbook for the year: tax sheets from the computation, then eTIMS, checks, statements, journal tests
    and the memo, each from the view the app shows. Parts that cannot be produced are listed with the reason."""
    import io

    from openpyxl import load_workbook

    seed = load_seed()
    cid = client.post("/clients", headers=PREPARER, json={"name": "Kilima Analytics Ltd"}).json()["id"]
    year = f"/clients/{cid}/years/2026-12-31"
    client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": "2026-12-31"},
                files={"file": ("tb.csv", seed.client_tb_csv(), "text/csv")})
    client.post(f"{year}/confirm-suggestions", headers=PREPARER)

    def pack():
        r = client.get(f"{year}/papers.xlsx", headers=PREPARER)
        assert r.status_code == 200, r.text
        assert 'filename="working-papers-2026-12-31.xlsx"' in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content), data_only=True)
        index = {row[0]: row[1:] for row in wb["Index"].values if row and row[0]}
        return wb, index

    # 5250 is still unconfirmed: the pack is produced, the tax part says why it is missing.
    wb, index = pack()
    assert wb.sheetnames == ["Index"]
    assert index["Tax computation"][0].startswith("Not produced:") and "5250" in index["Tax computation"][0]
    assert index["eTIMS reconciliation"][0].startswith("Not produced")

    client.put(f"/clients/{cid}/mappings/5250", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "disallowable"})
    client.post(f"{year}/fixed-assets", headers=PREPARER,
                files={"file": ("far.csv", seed.FIXED_ASSET_REGISTER, "text/csv")})
    client.put(f"{year}/inputs", headers=PREPARER, json=seed.TAX_INPUTS)
    for path, name, data in (("ledger", "purchases.csv", seed.PURCHASE_LEDGER),
                             ("invoices", "etims.csv", seed.ETIMS_PURCHASES)):
        assert client.post(f"{year}/etims/{path}", headers=PREPARER,
                           files={"file": (name, data, "text/csv")}).status_code in (200, 201)
    for kind, data in (("vat", seed.VAT_RETURNS), ("paye", seed.PAYE_RETURNS)):
        assert client.post(f"{year}/checks/returns/{kind}", headers=PREPARER,
                           files={"file": (f"{kind}.csv", data, "text/csv")}).status_code in (200, 201)
    for kind, label, data in (("mpesa", "M-Pesa Till", seed.MPESA_STATEMENT), ("bank", "Equity", seed.BANK_STATEMENT)):
        assert client.post(f"{year}/statements", headers=PREPARER, data={"kind": kind, "label": label},
                           files={"file": (f"{kind}.csv", data, "text/csv")}).status_code in (200, 201)
    assert client.post(f"{year}/ledger", headers=PREPARER, data={"approval_limit": "5,000,000"},
                       files={"file": ("gl.csv", seed.general_ledger_csv(), "text/csv")}).status_code in (200, 201)

    wb, index = pack()
    assert wb.sheetnames == ["Index", "Analysis", "Computation", "Lead Schedule", "Capital Allowances",
                             "Asset Tax Detail", "Credits", "eTIMS", "Checks", "Statements", "Statement Lines",
                             "Journal Tests", "Memo"]
    assert not any(str(v[0]).startswith("Not produced") for k, v in index.items() if k in (
        "Tax computation", "eTIMS reconciliation", "Compliance checks", "Bank and M-Pesa statements",
        "Journal entry tests", "Board tax memo"))
    assert index["Tax computation"][0] == "Not approved by a reviewer"
    # The same figure as the Tax tab: taxable profit 193,600,000 (worked in the test above).
    computation = {row[0]: row[1] for row in wb["Computation"].values}
    assert computation["Taxable profit"] == 193_600_000
    # Every eTIMS row names its purchase ledger row; every statement line its statement row.
    etims_rows = list(wb["eTIMS"].values)[1:]
    assert etims_rows and all(row[-1] for row in etims_rows)
    assert all(" row " in row[-1] for row in list(wb["Statement Lines"].values)[1:])
    # The VAT check (1,148m declared against 1,160m revenue) is open until explained.
    assert any(row[6] == "To be explained" for row in wb["Checks"].values)
