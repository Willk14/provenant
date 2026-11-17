"""Double-entry journal entries. An unbalanced entry cannot be constructed."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from provenant.core.money import ZERO, round_money


@dataclass(frozen=True)
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    def __post_init__(self) -> None:
        if not self.account:
            raise ValueError("line needs an account")
        if self.debit < 0 or self.credit < 0:
            raise ValueError("debit and credit must be non-negative")
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("a line is either a debit or a credit")
        if round_money(self.debit) != self.debit or round_money(self.credit) != self.credit:
            raise ValueError("line amounts must already be rounded to cents")


@dataclass(frozen=True)
class JournalEntry:
    on: date
    memo: str
    lines: tuple[Line, ...]

    def __post_init__(self) -> None:
        if len(self.lines) < 2:
            raise ValueError("an entry needs at least two lines")
        debits = sum((line.debit for line in self.lines), ZERO)
        credits = sum((line.credit for line in self.lines), ZERO)
        if debits != credits:
            raise ValueError(f"unbalanced entry {self.memo!r}: debits {debits} != credits {credits}")


def pair(debit_account: str, credit_account: str, amount: Decimal) -> tuple[Line, ...]:
    """Two lines moving `amount` from credit to debit account. Negative swaps sides; zero yields nothing."""
    if amount == 0:
        return ()
    if amount < 0:
        debit_account, credit_account, amount = credit_account, debit_account, -amount
    return (Line(debit_account, debit=amount), Line(credit_account, credit=amount))
