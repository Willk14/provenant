"""The practice engines, each with its hand-worked expectation."""
from datetime import date, timedelta
from decimal import Decimal as D

from provenant.practice import completeness as kyc
from provenant.practice.conflicts import Known, Party, check, normalise, similarity
from provenant.practice.trust import BankLine, Entry, match, reconcile


def test_names_match_whatever_the_suffix_order_or_punctuation():
    assert normalise("Kilima Analytics Ltd.") == normalise("KILIMA ANALYTICS LIMITED") == "analytics kilima"
    assert similarity("Otieno, Grace Akinyi", "Grace Akinyi Otieno") == 1.0
    assert similarity("Grace Otieno", "Grace Akinyi Otieno") == 0.9  # two words, both inside the longer name
    assert similarity("Grace", "Grace Akinyi Otieno") < 0.85  # one word is too common to call a match
    assert similarity("", "Anything") == 0.0


def test_conflict_hits_are_ranked_screening_then_adverse():
    new = [Party("Kilima Analytics Ltd", "client", "PVT-123"), Party("Rift Valley Bank plc", "counterparty")]
    known = [
        Known("Rift Valley Bank Limited", "client", "M-2024-007"),  # we act for the other side's bank: adverse
        Known("Kilima Analytics", "counterparty", "M-2025-011"),
        Known("Totally Different Holdings", "client", "M-2025-002"),
        Known("K. Analytics", "watchlist", "UN consolidated list", "PVT123"),  # same registration number
    ]
    hits = check(new, known)
    # Both adverse hits are exact names (score 1.0), so they are ordered by the new party's name.
    assert [(h.kind, h.source) for h in hits] == [
        ("screening", "UN consolidated list"), ("adverse", "M-2025-011"), ("adverse", "M-2024-007")]
    assert hits[0].reason == "same ID or registration number" and hits[1].score == 1.0


def test_file_completeness_depends_on_the_client_and_the_money():
    today = date(2026, 11, 24)
    assert kyc.required("person", False) == ["engagement_letter", "client_id"]
    assert kyc.required("organisation", True) == ["engagement_letter", "client_id", "beneficial_ownership",
                                                   "source_of_funds"]
    items = [kyc.Item("engagement_letter", "verified"), kyc.Item("client_id", "verified", date(2026, 12, 20)),
             kyc.Item("beneficial_ownership", "provided")]
    found = {g.item: g.problem for g in kyc.gaps(kyc.required("organisation", True), items, today)}
    # The ID expires in 26 days, inside the 60-day warning; ownership is on file but unverified; funds are missing.
    assert found == {"client_id": "expires soon", "beneficial_ownership": "not verified", "source_of_funds": "missing"}


def test_only_the_latest_practising_certificate_counts():
    today = date(2026, 11, 24)
    certs = [kyc.Certificate("pam@firm", date(2025, 12, 31)), kyc.Certificate("pam@firm", date(2026, 12, 31)),
             kyc.Certificate("rae@firm", date(2026, 6, 30))]
    found = {g.label: g.problem for g in kyc.certificate_gaps(["pam@firm", "rae@firm", "zed@firm"], certs, today)}
    # Pam's 2026 certificate expires in 37 days; Rae's expired in June; Zed has none.
    assert found == {"Practising certificate: pam@firm": "expires soon", "Practising certificate: rae@firm": "expired",
                     "Practising certificate: zed@firm": "missing"}


def entry(id, on, amount, matter="M1", reference=""):
    return Entry(id, on, D(amount), reference, matter)


def test_matching_prefers_the_reference_then_the_nearest_date():
    e1, e2 = entry("e1", date(2026, 11, 2), "100"), entry("e2", date(2026, 11, 4), "100", reference="RCPT-9")
    pairs = match([e1, e2], [BankLine("b1", date(2026, 11, 5), D("100"), "rcpt 9", "deposit")])
    assert [(e.id, b.id) for e, b in pairs] == [("e2", "b1")]  # the reference wins over e1
    assert match([e1], [BankLine("b2", date(2026, 11, 9), D("100"), "", "late")]) == []  # 7 days: outside the window


def test_three_way_reconciliation_balances_with_items_in_transit():
    as_of = date(2026, 11, 30)
    cashbook = [
        entry("r1", date(2026, 11, 3), "500000.00", "M1"),  # receipt for client 1
        entry("r2", date(2026, 11, 10), "200000.00", "M2"),  # receipt for client 2
        entry("p1", date(2026, 11, 20), "-150000.00", "M1"),  # payment on behalf of client 1
        entry("r3", date(2026, 11, 29), "75000.00", "M2"),  # deposited on the 29th, not on the statement yet
    ]
    lines = [BankLine("b1", date(2026, 11, 3), D("500000.00"), "", "deposit"),
             BankLine("b2", date(2026, 11, 11), D("200000.00"), "", "deposit"),
             BankLine("b3", date(2026, 11, 21), D("-150000.00"), "", "cheque 001")]
    result = reconcile(as_of, D("550000.00"), cashbook, cashbook, lines)
    # Bank 550,000 + 75,000 in transit = 625,000. Cashbook 500,000 + 200,000 - 150,000 + 75,000 = 625,000.
    # Ledgers: client 1 = 350,000, client 2 = 275,000, total 625,000.
    assert (result.adjusted_bank, result.cashbook, result.ledgers_total) == (D("625000.00"),) * 3
    assert [e.id for e in result.outstanding] == ["r3"] and result.balanced and result.alerts == ()


def test_overdrawn_unallocated_unrecorded_and_dormant_raise_alerts():
    as_of = date(2026, 11, 30)
    cashbook = [
        entry("r1", date(2025, 1, 10), "40000", "M9"),  # untouched since January 2025
        entry("p1", date(2026, 11, 5), "-10000", "M1"),  # paid for client 1 with no money held: overdrawn
        entry("x1", date(2026, 11, 6), "5000", None),  # received but not allocated to any client
    ]
    lines = [BankLine("b1", date(2026, 11, 5), D("-10000"), "", "cheque"),
             BankLine("b2", date(2026, 11, 6), D("5000"), "", "deposit"),
             BankLine("b3", date(2026, 11, 28), D("-350"), "", "bank charges")]
    result = reconcile(as_of, D("34650"), cashbook, cashbook[1:], lines, dormant_after=timedelta(days=365))
    kinds = sorted((a.kind, a.severity) for a in result.alerts)
    # Cashbook 40,000 - 10,000 + 5,000 = 35,000. Bank 34,650 with nothing in transit, so 350 short: the bank charge.
    # Ledgers 40,000 - 10,000 = 30,000, which is the cashbook less the 5,000 unallocated.
    assert result.adjusted_bank == D("34650") and result.cashbook == D("35000") and result.ledgers_total == D("30000")
    assert kinds == [("break", "hard"), ("break", "hard"), ("dormant", "warning"), ("overdrawn", "hard"),
                     ("unallocated", "hard"), ("unrecorded", "hard")]
    assert not result.balanced
