from datetime import date
from decimal import Decimal

import pytest

from provenant.core.money import round_money, to_decimal
from provenant.ledger import JournalEntry, Line, pair


def test_money_rejects_floats_and_parses_strings():
    with pytest.raises(TypeError):
        to_decimal(0.1)
    assert to_decimal("1,250.50") == Decimal("1250.50")
    with pytest.raises(ValueError):
        to_decimal("NaN")


def test_round_half_up():
    assert round_money(Decimal("2.345")) == Decimal("2.35")


def test_unbalanced_entry_cannot_exist():
    with pytest.raises(ValueError, match="unbalanced"):
        JournalEntry(date(2026, 1, 1), "bad", (Line("A", debit=Decimal("1.00")), Line("B", credit=Decimal("0.99"))))


def test_line_rejects_unrounded_and_two_sided():
    with pytest.raises(ValueError):
        Line("A", debit=Decimal("1.001"))
    with pytest.raises(ValueError):
        Line("A", debit=Decimal("1"), credit=Decimal("1"))


def test_pair_swaps_negative_and_skips_zero():
    assert pair("A", "B", Decimal("0")) == ()
    debit, credit = pair("A", "B", Decimal("-5.00"))
    assert (debit.account, credit.account) == ("B", "A")
