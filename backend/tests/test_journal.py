from datetime import date
from decimal import Decimal

import pytest

from provenant.analytics.journal import LedgerError, analyse, entry_lines, load

D = Decimal
YEAR = (date(2026, 1, 1), date(2026, 12, 31))

# Weekdays: 1 Jan 2026 is a Thursday, so 2 Mar is a Monday, 7 Mar a Saturday, 15 Apr a Wednesday, 5 May a
# Tuesday, 1-5 Jun Monday to Friday, 30 Dec a Wednesday and 2 Jan 2027 a Saturday.
GL = """entry_id,date,posted_at,account_code,description,user,debit,credit
E1,2026-03-02,2026-03-02 10:00,1000,Sale INV-1,pat,"50,000.00",
E1,2026-03-02,2026-03-02 10:00,4000,Sale INV-1,pat,,50000
E2,2026-03-02,2026-03-02 10:00,1000,Sale INV-1,pat,50000,
E2,2026-03-02,2026-03-02 10:00,4000,Sale INV-1,pat,,50000
E3,2026-03-07,2026-03-07 10:00,5200,Stationery,pat,12345.67,
E3,2026-03-07,2026-03-07 10:00,1000,Stationery,pat,,12345.67
E4,2026-04-15,2026-04-15 22:30,5300,Consulting,temp1,500000,
E4,2026-04-15,2026-04-15 22:30,1000,Consulting,temp1,,500000
E5,2026-05-05,2026-05-05 10:00,5200,Repairs,pat,1000,
E5,2026-05-05,2026-05-05 10:00,1000,Repairs,pat,,900
E6,30/12/2026,2026-12-30 10:00,5200,Year-end accrual,pat,9500,
E6,30/12/2026,2026-12-30 10:00,1000,Year-end accrual,pat,,9500
E7,2027-01-02,2027-01-02 10:00,6999,Late adjustment,pat,300,
E7,2027-01-02,2027-01-02 10:00,1000,Late adjustment,pat,,300
""" + "".join(
    f"F{d},2026-06-0{d},2026-06-0{d} 10:00,1000,Cash sale,rae,1234,\n"
    f"F{d},2026-06-0{d},2026-06-0{d} 10:00,4000,Cash sale,rae,,1234\n" for d in range(1, 6))


@pytest.fixture
def ledger(tmp_path):
    csv, parquet = tmp_path / "gl.csv", tmp_path / "gl.parquet"
    csv.write_text(GL, encoding="utf-8")
    assert load(csv, parquet) == 24
    return parquet


def test_each_journal_test_finds_what_it_should(ledger):
    # P&L balances per the trial balance: revenue 2 x 50,000 + 5 x 1,234 = 106,170 (credit); 5200 = 12,345.67 +
    # 1,000 + 9,500 = 22,845.67; 5300 is 450,000 in the TB but 500,000 in the ledger.
    a = analyse(ledger, *YEAR, approval_limit=D(10_000),
                pl_balances={"4000": D(-106_170), "5200": D("22845.67"), "5300": D(450_000)})
    assert (a.lines, a.entries) == (24, 12)
    hits = {t.key: sorted(f.entry_id for f in t.sample) for t in a.tests}
    assert hits == {
        "unbalanced": ["E5"],  # debits 1,000, credits 900
        "duplicates": ["E1", "E2"],
        "round_amounts": ["E4"],  # 500,000
        "weekend": ["E3", "E7"],
        "after_hours": ["E4"],  # 22:30
        "rare_users": ["E4"],  # temp1: 1 entry; cutoff max(2, 12 / 100) = 2
        "rare_accounts": ["E4", "E7"],  # 5300 and 6999 are used once each
        "period_end": ["E6", "E7"],  # after 26 Dec 2026, and dated after the year end
        "near_limit": ["E6"],  # 9,500 is within 10% below 10,000
    }
    unbalanced = next(t for t in a.tests if t.key == "unbalanced")
    assert unbalanced.sample[0].detail == "debits less credits 100.00"
    # E4 trips four tests, E7 three: they lead the review list
    assert [(e, tests) for e, _, tests in a.risk[:2]] == [
        ("E4", ("round_amounts", "after_hours", "rare_users", "rare_accounts")),
        ("E7", ("weekend", "rare_accounts", "period_end"))]
    assert [(r.account_code, r.difference) for r in a.reconciliation] == [
        ("4000", D(0)), ("5200", D(0)), ("5300", D(50_000))]
    assert a.benford is None  # fewer than 100 lines of 10 or more
    lines = entry_lines(ledger, ["E5"])
    assert [(r[4], r[8]) for r in lines] == [("5200", D("1000.00")), ("1000", D("-900.00"))]


def test_tests_needing_a_column_say_so(tmp_path):
    csv, parquet = tmp_path / "gl.csv", tmp_path / "gl.parquet"
    csv.write_text("entry_id,date,account_code,amount\nA,2026-01-05,1000,10\nA,2026-01-05,4000,-10\n", encoding="utf-8")
    load(csv, parquet)
    applies = {t.key: t.applies for t in analyse(parquet, *YEAR).tests}
    assert not applies["after_hours"] and not applies["rare_users"] and not applies["near_limit"]


def test_benford_conformity(tmp_path):
    csv, parquet = tmp_path / "gl.csv", tmp_path / "gl.parquet"
    # Log-uniform amounts from 10 to 10,000 follow Benford's law; a ledger of amounts all starting with 5 does not.
    natural = "".join(f"N{i},2026-02-02,5200,{10 ** (1 + i * 3 / 1000):.2f}\n" for i in range(1000))
    csv.write_text("entry_id,date,account_code,amount\n" + natural, encoding="utf-8")
    load(csv, parquet)
    assert analyse(parquet, *YEAR).benford.conformity == "close"
    csv.write_text("entry_id,date,account_code,amount\n" + "".join(f"F{i},2026-02-02,5200,5{i:03d}.00\n" for i in range(200)),
                   encoding="utf-8")
    load(csv, parquet)
    b = analyse(parquet, *YEAR).benford
    assert b.conformity == "nonconformity" and b.observed[4] == 1.0  # every first digit is 5


def test_bad_ledgers_are_refused_with_the_row(tmp_path):
    csv, parquet = tmp_path / "gl.csv", tmp_path / "gl.parquet"
    csv.write_text("entry_id,date,account_code,amount\nA,2026-13-45,1000,10\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="row 2: date 2026-13-45 is not a date"):
        load(csv, parquet)
    csv.write_text("entry,date,amount\nA,2026-01-01,1\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="columns needed"):
        load(csv, parquet)
