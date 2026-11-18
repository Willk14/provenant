"""Kenya corporate instalment-tax planner. Pure, deterministic Decimal/date calculations."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from provenant.core.money import ZERO, round_money


@dataclass(frozen=True)
class Instalment:
    number: int
    due_date: date
    proportion: Decimal
    amount: Decimal
    paid_to_date: Decimal
    outstanding: Decimal
    basis: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class InstalmentPlan:
    annual_amount: Decimal
    basis_used: str
    prior_year_basis: Decimal | None
    current_year_basis: Decimal
    withholding_reduction: Decimal
    agriculture_income_ratio: Decimal
    installments: tuple[Instalment, ...]
    balance_due_date: date
    balance_due_basis: str
    balance_due_amount: Decimal


def plan_instalments(
    year_end: date,
    current_year_tax: Decimal,
    prior_year_assessed_tax: Decimal | None,
    withholding: Decimal,
    agriculture_income_ratio: Decimal,
    instalment_payments: tuple[tuple[date, Decimal], ...] = (),
    balance_due_amount: Decimal = ZERO,
) -> InstalmentPlan:
    """Plan payments using s.12 and Twelfth Schedule; caller supplies sourced facts."""
    if not ZERO <= agriculture_income_ratio <= 1:
        raise ValueError("agriculture income ratio must be between 0 and 1")
    if current_year_tax < ZERO or withholding < ZERO:
        raise ValueError("tax and withholding amounts cannot be negative")
    if prior_year_assessed_tax is not None and prior_year_assessed_tax < ZERO:
        raise ValueError("prior-year assessed tax cannot be negative")
    if any(amount < ZERO for _, amount in instalment_payments):
        raise ValueError("instalment payments cannot be negative")

    current_basis = max(current_year_tax - withholding, ZERO)
    prior_basis = round_money(prior_year_assessed_tax * Decimal("1.10")) if prior_year_assessed_tax is not None else None
    candidates = [("current-year estimate", current_basis)]
    if prior_basis is not None:
        candidates.append(("110% of prior-year assessment", max(prior_basis - withholding, ZERO)))
    basis_used, annual = min(candidates, key=lambda item: item[1])

    start_month = year_end.month % 12 + 1
    start_year = year_end.year if year_end.month == 12 else year_end.year - 1
    due_months = (9, 12) if agriculture_income_ratio > Decimal(2) / Decimal(3) else (4, 6, 9, 12)
    proportions = (Decimal("0.75"), Decimal("0.25")) if len(due_months) == 2 else (Decimal("0.25"),) * 4
    due_dates = [date(start_year + (start_month - 1 + offset - 1) // 12,
                      (start_month - 1 + offset - 1) % 12 + 1, 20) for offset in due_months]
    payments = sorted(instalment_payments)
    cumulative_paid = ZERO
    rows = []
    allocated = ZERO
    for idx, (due, proportion) in enumerate(zip(due_dates, proportions)):
        amount = annual - allocated if idx == len(proportions) - 1 else round_money(annual * proportion)
        allocated += amount
        cumulative_paid += sum((value for paid_on, value in payments if paid_on <= due), ZERO)
        payments = [(paid_on, value) for paid_on, value in payments if paid_on > due]
        due_to_date = sum((row.amount for row in rows), ZERO) + amount
        paid_allocated = min(cumulative_paid, due_to_date)
        previously_paid = sum((row.paid_to_date for row in rows), ZERO)
        paid_this_period = max(paid_allocated - previously_paid, ZERO)
        rows.append(Instalment(
            number=idx + 1, due_date=due, proportion=proportion, amount=amount,
            paid_to_date=paid_this_period, outstanding=max(amount - paid_this_period, ZERO),
            basis=("Income Tax Act s.12 and Twelfth Schedule; "+
                   ("agricultural income exceeds two-thirds: 75% / 25%" if len(due_months) == 2 else "25% per instalment")),
            sources=("current_year_tax_computation", "prior_year_assessment" if prior_year_assessed_tax is not None else "preparer_current_year_estimate", "withholding_credits", "instalment_payment_records"),
        ))
    balance_month = year_end.month % 12 + 1
    balance_year = year_end.year + (1 if year_end.month == 12 else 0)
    fourth_after = balance_month + 3
    balance_due = date(balance_year + (fourth_after - 1) // 12, (fourth_after - 1) % 12 + 1, 1)
    from calendar import monthrange
    balance_due = balance_due.replace(day=monthrange(balance_due.year, balance_due.month)[1])
    return InstalmentPlan(annual, basis_used, prior_basis, current_basis, withholding,
                          agriculture_income_ratio, tuple(rows), balance_due,
                          "Income Tax Act s.52B: balance of tax due by the last day of the fourth month after the accounting period",
                          balance_due_amount)
