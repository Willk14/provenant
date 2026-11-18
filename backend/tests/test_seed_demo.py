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
