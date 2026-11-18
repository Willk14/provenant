"""Financial mapping: each trial balance account maps to a financial statement line and a tax treatment.

Suggestions are deterministic: first the firm's own confirmed mappings for the same account name at other
clients (when they clearly agree), then keyword rules over account names. The same inputs always give the
same suggestions, and a person confirms every mapping before it is used.
"""
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Statement(StrEnum):
    POSITION = "position"  # statement of financial position
    PROFIT_OR_LOSS = "profit_or_loss"


class TaxTreatment(StrEnum):
    TAXABLE = "taxable"  # income charged to tax
    EXEMPT = "exempt"  # income not charged to tax (deducted in the computation)
    DEDUCTIBLE = "deductible"  # expense allowed (ITA s.15)
    DISALLOWABLE = "disallowable"  # expense not allowed (ITA s.16): added back
    DEPRECIATION = "depreciation"  # accounting depreciation/amortisation: added back, capital allowances instead
    LEASE_INTEREST = "lease_interest"  # IFRS 16 interest: added back, rentals paid deducted instead
    BALANCE_SHEET = "balance_sheet"  # not a profit or loss item
    TAX_CHARGE = "tax_charge"  # the income tax expense itself: excluded from profit before tax


@dataclass(frozen=True)
class Line:
    statement: Statement
    label: str
    default_treatment: TaxTreatment
    credit_nature: bool  # liabilities, equity and income carry credit balances


P, B = Statement.PROFIT_OR_LOSS, Statement.POSITION
T = TaxTreatment

FS_LINES: dict[str, Line] = {
    # statement of financial position
    "ppe_cost": Line(B, "Property, plant and equipment - cost", T.BALANCE_SHEET, False),
    "ppe_accumulated_depreciation": Line(B, "Property, plant and equipment - accumulated depreciation", T.BALANCE_SHEET, False),
    "right_of_use_assets": Line(B, "Right-of-use assets", T.BALANCE_SHEET, False),
    "intangible_assets": Line(B, "Intangible assets", T.BALANCE_SHEET, False),
    "inventories": Line(B, "Inventories", T.BALANCE_SHEET, False),
    "trade_receivables": Line(B, "Trade and other receivables", T.BALANCE_SHEET, False),
    "cash": Line(B, "Cash and cash equivalents", T.BALANCE_SHEET, False),
    "trade_payables": Line(B, "Trade and other payables", T.BALANCE_SHEET, True),
    "borrowings": Line(B, "Borrowings", T.BALANCE_SHEET, True),
    "lease_liabilities": Line(B, "Lease liabilities", T.BALANCE_SHEET, True),
    "current_tax": Line(B, "Current tax payable", T.BALANCE_SHEET, True),
    "deferred_tax": Line(B, "Deferred tax", T.BALANCE_SHEET, True),
    "share_capital": Line(B, "Share capital", T.BALANCE_SHEET, True),
    "retained_earnings": Line(B, "Retained earnings", T.BALANCE_SHEET, True),
    # profit or loss
    "revenue": Line(P, "Revenue", T.TAXABLE, True),
    "other_income": Line(P, "Other income", T.TAXABLE, True),
    "cost_of_sales": Line(P, "Cost of sales", T.DEDUCTIBLE, False),
    "employee_costs": Line(P, "Employee costs", T.DEDUCTIBLE, False),
    "operating_expenses": Line(P, "Other operating expenses", T.DEDUCTIBLE, False),
    "depreciation_amortisation": Line(P, "Depreciation and amortisation", T.DEPRECIATION, False),
    "finance_costs": Line(P, "Finance costs", T.DEDUCTIBLE, False),
    "income_tax_expense": Line(P, "Income tax expense", T.TAX_CHARGE, False),
}

# Ordered: the first rule that matches an account name wins. Treatment overrides the line's default.
# Order matters: the first rule that matches wins. Two principles, each learned from a test or a dry run on a
# realistic Kenyan trial balance:
#   1. Specific before general ("Cost of sales" is not revenue; "Interest on lease liabilities" is not a lease
#      liability; "KRA penalties" is not tax payable).
#   2. Profit-or-loss signals before balance-sheet nouns: "Depreciation - right-of-use asset" is depreciation,
#      "Interest on term loan" is a finance cost, and "Term loan - Rift Valley Bank" is a loan, not cash.
_RULES: list[tuple[str, str, TaxTreatment | None]] = [
    # contra-assets and income first, so the expense rules below cannot take them
    (r"accum\w*\s+depreciation|provision for depreciation", "ppe_accumulated_depreciation", None),
    (r"dividend income|dividends received", "other_income", T.EXEMPT),
    (r"interest income|interest received|other income|gain on|rental income", "other_income", None),
    # profit-or-loss signals
    (r"interest on lease|lease interest", "finance_costs", T.LEASE_INTEREST),
    (r"fines?\b|penalt", "operating_expenses", T.DISALLOWABLE),
    (r"donation", "operating_expenses", T.DISALLOWABLE),
    (r"entertainment", "operating_expenses", T.DISALLOWABLE),
    (r"depreciation|amorti[sz]ation", "depreciation_amortisation", None),
    (r"income tax expense|corporation tax|tax charge", "income_tax_expense", None),
    (r"interest|finance cost|bank charges", "finance_costs", None),
    (r"cost of (sales|goods)|purchases|direct cost", "cost_of_sales", None),
    (r"revenue|sales|turnover|fees? earned|subscriptions?", "revenue", None),
    (r"salar|wage|staff|payroll|nssf|nhif|shif|pension|employee", "employee_costs", None),
    # balance-sheet nouns; loans before "bank", so a bank's loan is not cash
    (r"right[- ]of[- ]use", "right_of_use_assets", None),
    (r"lease liabilit", "lease_liabilities", None),
    (r"deferred tax", "deferred_tax", None),
    (r"tax payable|current tax", "current_tax", None),
    (r"loan|borrowing|overdraft|facility", "borrowings", None),
    (r"property|plant|equipment|motor vehicle|furniture|computers?\b|buildings?", "ppe_cost", None),
    (r"software|goodwill|intangible", "intangible_assets", None),
    (r"inventor|stock", "inventories", None),
    (r"receivable|debtor|prepayment", "trade_receivables", None),
    (r"\bbank\b|\bcash\b|m-?pesa|petty cash", "cash", None),
    (r"payable|creditor|accrual", "trade_payables", None),
    (r"share capital|ordinary shares", "share_capital", None),
    (r"retained|reserves?\b|accumulated (profit|loss)", "retained_earnings", None),
    # general operating expenses last
    (r"expense|administrative|marketing|advertis|travel|utilities|repairs|insurance|professional fees|audit fee",
     "operating_expenses", None),
]


@dataclass(frozen=True)
class Suggestion:
    fs_line: str | None  # None: no rule matched; a person must choose
    tax_treatment: TaxTreatment | None
    rule: str | None  # the pattern that matched, kept as the suggestion's reason


def suggest(account_name: str) -> Suggestion:
    name = account_name.lower()
    for pattern, line, treatment in _RULES:
        if re.search(pattern, name):
            return Suggestion(line, treatment or FS_LINES[line].default_treatment, pattern)
    return Suggestion(None, None, None)  # unknown names stay unmapped rather than guessed


FIRM_RULE_PREFIX = "firm: "


@dataclass(frozen=True)
class Precedent:
    """A confirmed mapping at another client of the same firm, by account name."""

    client_id: str
    account_name: str
    fs_line: str
    tax_treatment: str


def normalise_name(account_name: str) -> str:
    """Case, punctuation and spacing do not make accounts different: 'Bank charges' == 'BANK-CHARGES'."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", account_name.casefold()).split())


def learn(precedents: Iterable[Precedent]) -> dict[str, Suggestion]:
    """Suggestions from the firm's confirmed mappings, keyed by normalised account name.

    Each client counts once per distinct mapping. A mapping is learned only when it holds at least two thirds
    of the clients with that account name and nothing ties with it; otherwise the keyword rules apply.
    """
    votes: dict[str, set[tuple[str, str, str]]] = {}
    for p in precedents:
        key = normalise_name(p.account_name)
        if key:
            votes.setdefault(key, set()).add((p.client_id, p.fs_line, p.tax_treatment))
    learned = {}
    for key, choices in votes.items():
        clients = len({client for client, _, _ in choices})
        ranked = Counter((line, treatment) for _, line, treatment in choices).most_common()
        (line, treatment), count = ranked[0]
        tied = len(ranked) > 1 and ranked[1][1] == count
        if tied or 3 * count < 2 * clients or line not in FS_LINES:
            continue
        noun = "client" if clients == 1 else "clients"
        learned[key] = Suggestion(line, TaxTreatment(treatment),
                                  f"{FIRM_RULE_PREFIX}confirmed this way for {count} of {clients} other {noun}")
    return learned


def suggest_for_firm(account_name: str, learned: dict[str, Suggestion]) -> Suggestion:
    """The firm's own practice first, then the keyword rules."""
    return learned.get(normalise_name(account_name)) or suggest(account_name)


def check_mapping(fs_line: str, tax_treatment: str) -> TaxTreatment:
    if fs_line not in FS_LINES:
        raise ValueError(f"unknown financial statement line {fs_line!r}")
    try:
        treatment = TaxTreatment(tax_treatment)
    except ValueError as exc:
        raise ValueError(f"unknown tax treatment {tax_treatment!r}") from exc
    line = FS_LINES[fs_line]
    if (line.statement is Statement.POSITION) != (treatment is TaxTreatment.BALANCE_SHEET):
        raise ValueError(f"{line.label} is a {line.statement} line; tax treatment {treatment} does not apply")
    return treatment
