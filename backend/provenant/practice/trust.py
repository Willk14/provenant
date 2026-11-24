"""Client trust account: three-way reconciliation and its hard alerts (ADR 0018). Pure and deterministic.

The three ways are the bank, the trust cashbook and the sum of the individual client ledgers. The bank balance is
adjusted for cashbook entries the bank has not shown yet (deposits in transit, unpresented payments). Every amount is a
Decimal. Nothing here moves money.
"""
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal("0")
MATCH_DAYS = 5  # a bank line may appear up to this many days after or before its cashbook entry


@dataclass(frozen=True)
class Entry:
    """A cashbook line. Receipts are positive and payments negative. `matter` is the client ledger it belongs to."""
    id: str
    on: date
    amount: Decimal
    reference: str
    matter: str | None


@dataclass(frozen=True)
class BankLine:
    id: str
    on: date
    amount: Decimal
    reference: str
    description: str


@dataclass(frozen=True)
class Ledger:
    matter: str
    balance: Decimal
    last_movement: date


@dataclass(frozen=True)
class Alert:
    kind: str  # break | overdrawn | unallocated | unrecorded | dormant
    severity: str  # hard | warning
    message: str
    amount: Decimal
    matter: str | None = None


@dataclass(frozen=True)
class Reconciliation:
    as_of: date
    bank_balance: Decimal
    outstanding: tuple[Entry, ...]  # in the cashbook, not yet on the bank statement
    unrecorded: tuple[BankLine, ...]  # on the bank statement, not in the cashbook
    matched: tuple[tuple[str, str], ...]  # (entry id, bank line id)
    adjusted_bank: Decimal
    cashbook: Decimal
    ledgers: tuple[Ledger, ...]
    ledgers_total: Decimal
    alerts: tuple[Alert, ...]

    @property
    def balanced(self) -> bool:
        return not any(a.severity == "hard" for a in self.alerts)


def _norm(reference: str) -> str:
    return "".join(ch for ch in reference.lower() if ch.isalnum())


def match(entries: Sequence[Entry], lines: Sequence[BankLine]) -> list[tuple[Entry, BankLine]]:
    """Pair each bank line with one cashbook entry of the same amount within MATCH_DAYS. A matching reference wins,
    then the nearest date. Each entry and line is used once."""
    pairs, used = [], set()
    for line in sorted(lines, key=lambda x: (x.on, x.id)):
        candidates = [e for e in entries if e.id not in used and e.amount == line.amount
                      and abs((e.on - line.on).days) <= MATCH_DAYS]
        if not candidates:
            continue
        ref = _norm(line.reference)
        best = min(candidates, key=lambda e: (not (ref and _norm(e.reference) == ref), abs((e.on - line.on).days), e.id))
        used.add(best.id)
        pairs.append((best, line))
    return pairs


def reconcile(as_of: date, bank_balance: Decimal, cashbook: Sequence[Entry], pool: Sequence[Entry],
              lines: Sequence[BankLine], dormant_after: timedelta | None = None) -> Reconciliation:
    """`cashbook` is every entry up to `as_of`. `pool` is the entries that may still be waiting at the bank: those in
    the statement period plus the previous reconciliation's outstanding items. `lines` is the statement up to as_of."""
    cashbook = [e for e in cashbook if e.on <= as_of]
    lines = [x for x in lines if x.on <= as_of]
    pairs = match([e for e in pool if e.on <= as_of], lines)
    matched_entries, matched_lines = {e.id for e, _ in pairs}, {x.id for _, x in pairs}
    outstanding = tuple(sorted((e for e in pool if e.on <= as_of and e.id not in matched_entries), key=lambda e: (e.on, e.id)))
    unrecorded = tuple(x for x in lines if x.id not in matched_lines)

    cash = sum((e.amount for e in cashbook), ZERO)
    adjusted = bank_balance + sum((e.amount for e in outstanding), ZERO)
    balances: dict[str, Decimal] = defaultdict(lambda: ZERO)
    last: dict[str, date] = {}
    unallocated = ZERO
    for e in cashbook:
        if e.matter is None:
            unallocated += e.amount
            continue
        balances[e.matter] += e.amount
        last[e.matter] = max(last.get(e.matter, e.on), e.on)
    ledgers = tuple(Ledger(m, balances[m], last[m]) for m in sorted(balances))
    ledgers_total = sum((x.balance for x in ledgers), ZERO)

    alerts = []
    if adjusted != cash:
        alerts.append(Alert("break", "hard", f"The adjusted bank balance {adjusted} does not equal the cashbook {cash}.",
                            adjusted - cash))
    if ledgers_total != cash:
        alerts.append(Alert("break", "hard", f"The client ledgers total {ledgers_total} but the cashbook is {cash}.",
                            ledgers_total - cash))
    if unallocated:
        alerts.append(Alert("unallocated", "hard", f"{unallocated} in the cashbook is not allocated to any client.",
                            unallocated))
    for x in ledgers:
        if x.balance < 0:
            alerts.append(Alert("overdrawn", "hard", f"Client ledger {x.matter} is overdrawn by {-x.balance}.",
                                x.balance, x.matter))
        elif dormant_after and x.balance > 0 and as_of - x.last_movement > dormant_after:
            alerts.append(Alert("dormant", "warning", f"{x.balance} held for {x.matter} has not moved since "
                                f"{x.last_movement.isoformat()}.", x.balance, x.matter))
    for x in unrecorded:
        alerts.append(Alert("unrecorded", "hard", f"The bank shows {x.amount} on {x.on.isoformat()} ({x.description}) "
                            "with no cashbook entry.", x.amount))
    return Reconciliation(as_of, bank_balance, outstanding, unrecorded, tuple((e.id, x.id) for e, x in pairs), adjusted,
                          cash, ledgers, ledgers_total, tuple(alerts))
