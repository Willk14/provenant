from decimal import Decimal

import pytest

from provenant.tax.engine import TaxCredit, compute
from provenant.tax.memo import DEFAULT, FIELD_NAMES, fields, render, tables
from tests.test_tax_engine import LEASE, PLANT, TB

D = Decimal


def memo(credits=(), text=DEFAULT):
    # The engine's worked example: taxable profit 140,000, tax 42,000, deferred tax liability 12,000
    c = compute(TB, [PLANT], [LEASE], losses_brought_forward=D(50_000), credits=credits)
    values = fields(c, "Kilima Analytics Ltd", "31 Dec 2026", approval="Approved by pam@firm-a.")
    return c, render(text, values, tables(c, [("VAT turnover against revenue", "Explained")]))


def text_of(block):
    return "".join(s.text for s in block.segments)


def test_every_figure_in_the_default_memo_is_bound():
    _, r = memo([TaxCredit("instalment", "Instalments", D(30_000))])
    summary = text_of(next(b for b in r.blocks if b.kind == "paragraph"))
    assert summary == ("Profit before tax is KES 243,000.00. After the adjustments below, taxable profit is "
                       "KES 140,000.00, and corporation tax at 30% is KES 42,000.00. After credits of KES 30,000.00, "
                       "the balance due to KRA is KES 12,000.00.")
    bound = [s.field for b in r.blocks for s in b.segments if s.field]
    assert {"profit_before_tax", "taxable_profit", "tax_payable", "total_credits", "tax_due_sentence"} <= set(bound)
    comp = next(b for b in r.blocks if b.table == "computation")
    assert comp.rows[0] == ("Profit before tax", "243,000.00", True)
    assert comp.rows[-1] == ("Tax due", "12,000.00", True)  # 42,000 - 30,000
    assert next(b for b in r.blocks if b.table == "checks").rows == (("VAT turnover against revenue", "Explained", False),)
    assert r.warnings == ()  # the default memo types no amounts


def test_a_changed_computation_changes_the_prose():
    _, refund = memo([TaxCredit("instalment", "Instalments", D(50_000))])
    summary = text_of(next(b for b in refund.blocks if b.kind == "paragraph"))
    assert summary.endswith("After credits of KES 50,000.00, KRA owes a refund of KES 8,000.00.")


def test_typed_amounts_are_flagged_and_unknown_fields_refused():
    _, r = memo(text="Tax payable is 42,000.00 ({{tax_payable}}), per section 16 for 2026.")
    assert r.warnings == ("'42,000.00' is typed, not bound to the computation: insert a field instead",)
    with pytest.raises(ValueError, match="unknown fields: tax_owed"):
        memo(text="Tax is {{tax_owed}}.")


def test_the_field_list_matches_what_a_computation_provides():
    c = compute(TB, [PLANT], [LEASE])
    assert tuple(fields(c, "x", "y")) == FIELD_NAMES
