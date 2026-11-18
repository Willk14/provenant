"""Corporate income tax computation and deferred tax, from a mapped trial balance. Deterministic.

Sign convention: trial balance lines are debit positive, credit negative. Profit before tax is income minus
expenses, so it is the negative of the sum of profit or loss balances (excluding the tax charge).
"""
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from provenant.core.money import ZERO, round_money
from provenant.tax.mapping import FS_LINES, Statement, TaxTreatment

T = TaxTreatment


@dataclass(frozen=True)
class Rules:
    """A versioned rule set. Only settled points live here; changeable rates are inputs (ADR 0006)."""

    jurisdiction: str
    version: str
    corporation_tax_rate: Decimal
    basis: dict[str, str]


KENYA = Rules(
    jurisdiction="KE",
    version="ke-2026.3",
    corporation_tax_rate=Decimal("0.30"),  # resident companies, Income Tax Act Third Schedule
    basis={
        "addback": "Income Tax Act s.16: deductions not allowed",
        "depreciation": "Accounting depreciation is not deductible; capital deductions under the Second Schedule apply",
        "capital_allowances": "Income Tax Act Second Schedule: capital deductions",
        "lease": "IFRS 16 charges reversed; lease rentals incurred in the year deducted (ITA s.15)",
        "exempt": "Income not chargeable to tax",
        "losses": "Income Tax Act s.15(4): losses carried forward",
        "rate": "Income Tax Act Third Schedule: resident corporation rate",
        "balancing_charge": "Income Tax Act Second Schedule: disposal proceeds above the written-down value are taxed",
        "disposal_gain": "Income Tax Act Second Schedule paragraph 3: disposal surplus is a trading receipt",
        "disposal_loss": "Income Tax Act Second Schedule paragraph 3: disposal proceeds below item residual value are a trading loss",
        "cessation": "Income Tax Act Second Schedule paragraph 4: balancing charge or deduction when the business ceases and the item ceases to be owned",
        "instalment": "Instalment tax paid for the year (Income Tax Act s.12)",
        "withholding": "Tax withheld at source, credited against the tax charged (hold the certificate)",
        "overpayment": "Overpayment brought forward from the prior year's return",
        "other_credit": "Other credit, on the basis stated by the preparer",
    },
)


@dataclass(frozen=True)
class MappedLine:
    id: str  # trial balance line id, kept so every computation line can name its sources
    code: str
    name: str
    balance: Decimal  # debit positive
    fs_line: str
    treatment: TaxTreatment


@dataclass(frozen=True)
class AllowanceAsset:
    """One register item with preparer-confirmed opening tax residual value."""

    code: str
    cost: Decimal
    opening_wdv: Decimal
    acquired_in_year: bool
    proceeds: Decimal = ZERO
    disposed: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("asset code is required")
        if min(self.cost, self.opening_wdv, self.proceeds) < 0:
            raise ValueError(f"{self.code}: asset values cannot be negative")


@dataclass(frozen=True)
class AllowanceAssetResult:
    code: str
    opening_wdv: Decimal
    additions: Decimal
    allowance: Decimal
    proceeds: Decimal
    disposal_result: Decimal | None
    closing_wdv: Decimal


@dataclass(frozen=True)
class AllowanceClass:
    """A capital deduction class. Rate and method are preparer inputs with a stated basis (Finance Acts change)."""

    name: str
    wdv_brought_forward: Decimal  # tax written-down value at the start of the year
    additions: Decimal
    rate: Decimal  # e.g. 0.25 for 25%
    method: str  # "reducing" (on WDV + additions) or "straight_line" (on cost: additions + original cost basis)
    basis: str
    cost_basis: Decimal = ZERO  # straight line: the qualifying cost the rate applies to (excluding additions)
    disposals: Decimal = ZERO  # proceeds from assets of this class disposed of in the year
    asset_opening_values: dict[str, Decimal] = field(default_factory=dict)
    assets: tuple[AllowanceAsset, ...] = ()
    business_ceased: bool = False

    def __post_init__(self) -> None:
        if not ZERO <= self.rate <= 1:
            raise ValueError(f"{self.name}: rate must be between 0 and 1")
        if self.method not in ("reducing", "straight_line"):
            raise ValueError(f"{self.name}: method must be reducing or straight_line")
        if min(self.wdv_brought_forward, self.additions, self.cost_basis, self.disposals) < 0:
            raise ValueError(f"{self.name}: values cannot be negative")
        if not self.basis.strip():
            raise ValueError(f"{self.name}: state the basis for the rate (e.g. the Second Schedule paragraph)")
        if any(not code.strip() or value < ZERO for code, value in self.asset_opening_values.items()):
            raise ValueError(f"{self.name}: asset opening values need a code and cannot be negative")

    def _pool(self) -> Decimal:
        return self.wdv_brought_forward + self.additions - self.disposals

    def allowance(self) -> Decimal:
        if self.assets:
            return sum((self._asset_allowance(a) for a in self.assets), ZERO)
        pool = max(self._pool(), ZERO)
        if self.method == "reducing":
            return round_money(pool * self.rate)
        return min(pool, round_money((self.cost_basis + self.additions) * self.rate))  # never below zero WDV

    def _asset_opening_base(self, asset: AllowanceAsset) -> Decimal:
        return asset.opening_wdv + (asset.cost if asset.acquired_in_year else ZERO)

    def _asset_allowance(self, asset: AllowanceAsset) -> Decimal:
        base = self._asset_opening_base(asset)
        if self.method == "reducing":
            return round_money(base * self.rate)
        return min(base, round_money(asset.cost * self.rate))

    def asset_disposal_results(self) -> tuple[tuple[str, Decimal], ...]:
        """Sale proceeds less the item's residual value after its current-year allowance."""
        return tuple(
            (asset.code, asset.proceeds - max(self._asset_opening_base(asset) - self._asset_allowance(asset), ZERO))
            for asset in self.assets if asset.disposed
        )

    def asset_schedule(self) -> tuple[AllowanceAssetResult, ...]:
        rows = []
        for asset in self.assets:
            base = self._asset_opening_base(asset)
            allowance = self._asset_allowance(asset)
            residual = max(base - allowance, ZERO)
            rows.append(AllowanceAssetResult(
                code=asset.code, opening_wdv=asset.opening_wdv,
                additions=asset.cost if asset.acquired_in_year else ZERO,
                allowance=allowance, proceeds=asset.proceeds if asset.disposed else ZERO,
                disposal_result=(asset.proceeds - residual) if asset.disposed else None,
                closing_wdv=ZERO if asset.disposed else residual,
            ))
        return tuple(rows)

    def balancing_charge(self) -> Decimal:
        """Disposal proceeds above the pool: the excess allowances are clawed back as income."""
        if self.assets:
            return self.cessation_charge() if self.business_ceased else ZERO
        return max(-self._pool(), ZERO)

    def disposal_gain(self) -> Decimal:
        if self.assets and not self.business_ceased:
            return sum((max(result, ZERO) for _, result in self.asset_disposal_results()), ZERO)
        return ZERO

    def disposal_loss(self) -> Decimal:
        if self.assets and not self.business_ceased:
            return sum((min(result, ZERO) for _, result in self.asset_disposal_results()), ZERO)
        return ZERO

    def cessation_charge(self) -> Decimal:
        if not self.business_ceased or not self.assets:
            return ZERO
        return sum((max(result, ZERO) for _, result in self.asset_disposal_results()), ZERO)

    def cessation_deduction(self) -> Decimal:
        if not self.business_ceased or not self.assets:
            return ZERO
        return sum((max(-result, ZERO) for _, result in self.asset_disposal_results()), ZERO)

    def wdv_carried_forward(self) -> Decimal:
        if self.assets:
            return sum((max(self._asset_opening_base(a) - self._asset_allowance(a), ZERO)
                        for a in self.assets if not a.disposed), ZERO)
        return max(self._pool(), ZERO) - self.allowance()


CREDIT_KINDS = {
    "instalment": "Instalment tax paid",
    "withholding": "Withholding tax credits",
    "overpayment": "Overpayment brought forward",
    "other": "Other credits",
}


@dataclass(frozen=True)
class TaxCredit:
    """Tax already paid or suffered for the year, set against the tax charged."""

    kind: str  # one of CREDIT_KINDS
    description: str
    amount: Decimal
    reference: str = ""  # receipt, certificate or return number
    evidence: str | None = None  # supporting document id

    def __post_init__(self) -> None:
        if self.kind not in CREDIT_KINDS:
            raise ValueError(f"credit kind must be one of {', '.join(CREDIT_KINDS)}")
        if self.amount <= 0:
            raise ValueError(f"{self.description or self.kind}: credit amount must be positive")
        if not self.description.strip():
            raise ValueError("each credit needs a description (e.g. the payer or the instalment)")


@dataclass(frozen=True)
class LeaseYear:
    """One lease's IFRS 16 figures for the tax year, from its verified Provenant schedule."""

    document_id: str
    name: str
    payments: Decimal  # rentals paid in the year
    rou_depreciation: Decimal
    interest: Decimal
    rou_carrying_amount: Decimal  # at year end
    liability: Decimal  # at year end


@dataclass(frozen=True)
class CompLine:
    label: str
    amount: Decimal  # signed contribution to taxable profit (add-backs positive, deductions negative)
    basis: str
    sources: tuple[str, ...] = ()  # trial balance line ids, lease document ids, or input names


@dataclass(frozen=True)
class Computation:
    rules_version: str
    profit_before_tax: Decimal
    adjustments: tuple[CompLine, ...]
    taxable_before_losses: Decimal
    losses_brought_forward: Decimal
    losses_used: Decimal
    losses_carried_forward: Decimal
    taxable_profit: Decimal
    rate: Decimal
    tax_payable: Decimal
    deferred: tuple[CompLine, ...]  # temporary differences (positive = taxable)
    deferred_tax: Decimal  # liability positive, asset negative
    credits: tuple[CompLine, ...] = ()  # negative: each reduces the tax still to pay
    tax_due: Decimal = ZERO  # tax payable less credits; negative means refundable
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _sum(lines: Sequence[MappedLine]) -> Decimal:
    return sum((line.balance for line in lines), ZERO)


def compute(
    lines: Sequence[MappedLine],
    allowances: Sequence[AllowanceClass] = (),
    leases: Sequence[LeaseYear] = (),
    losses_brought_forward: Decimal = ZERO,
    rules: Rules = KENYA,
    credits: Sequence[TaxCredit] = (),
) -> Computation:
    if losses_brought_forward < 0:
        raise ValueError("losses brought forward cannot be negative")
    pl = [l for l in lines if FS_LINES[l.fs_line].statement is Statement.PROFIT_OR_LOSS]
    by_treatment = lambda t: [l for l in pl if l.treatment is t]  # noqa: E731
    ids = lambda ls: tuple(l.id for l in ls)  # noqa: E731
    warnings: list[str] = []

    if any(c.business_ceased and not c.assets for c in allowances):
        raise ValueError("business-cessation balancing results require a matched per-asset register")

    profit_before_tax = -_sum([l for l in pl if l.treatment is not T.TAX_CHARGE])
    adjustments: list[CompLine] = []

    def add(label: str, amount: Decimal, basis_key: str, sources: tuple[str, ...]) -> None:
        if amount:
            adjustments.append(CompLine(label, amount, rules.basis[basis_key], sources))

    disallowed = by_treatment(T.DISALLOWABLE)
    add("Disallowable expenses added back", _sum(disallowed), "addback", ids(disallowed))
    depreciation = by_treatment(T.DEPRECIATION)
    add("Accounting depreciation and amortisation added back", _sum(depreciation), "depreciation", ids(depreciation))
    lease_interest = by_treatment(T.LEASE_INTEREST)
    add("IFRS 16 lease interest added back", _sum(lease_interest), "lease", ids(lease_interest))
    exempt = by_treatment(T.EXEMPT)
    add("Exempt income deducted", _sum(exempt), "exempt", ids(exempt))  # credit balances: negative

    for c in allowances:
        allowance_sources = tuple(f"asset:{a.code}" for a in c.assets) or (f"allowance:{c.name}",)
        add(f"Capital deduction: {c.name}", -c.allowance(), "capital_allowances", allowance_sources)
        if c.assets and c.business_ceased:
            for code, result in c.asset_disposal_results():
                if result > ZERO:
                    add(f"Balancing charge on cessation: {c.name} · {code}", result, "cessation", (f"asset:{code}",))
                elif result < ZERO:
                    add(f"Balancing deduction on cessation: {c.name} · {code}", -abs(result), "cessation", (f"asset:{code}",))
        elif c.assets:
            for code, result in c.asset_disposal_results():
                if result > ZERO:
                    add(f"Disposal gain: {c.name} · {code}", result, "disposal_gain", (f"asset:{code}",))
                elif result < ZERO:
                    add(f"Disposal loss: {c.name} · {code}", result, "disposal_loss", (f"asset:{code}",))
        else:
            add(f"Balancing charge: {c.name}", c.balancing_charge(), "balancing_charge", (f"allowance:{c.name}",))
    if leases:
        rentals = sum((l.payments for l in leases), ZERO)
        add("Lease rentals paid in the year deducted", -rentals, "lease", tuple(l.document_id for l in leases))
        # Right-of-use depreciation already sits in the depreciation accounts added back above; the lease
        # schedules confirm the amount, so a mismatch is flagged rather than double counted.
        rou = sum((l.rou_depreciation for l in leases), ZERO)
        if rou > _sum(depreciation):
            warnings.append(
                f"right-of-use depreciation per lease schedules ({rou}) exceeds depreciation in the trial balance"
            )
        interest = sum((l.interest for l in leases), ZERO)
        if lease_interest and interest != _sum(lease_interest):
            warnings.append(
                f"lease interest per schedules ({interest}) differs from the trial balance ({_sum(lease_interest)})"
            )
    elif lease_interest:
        warnings.append("lease interest is in the trial balance but no leases are linked to this client")

    taxable_before = profit_before_tax + sum((a.amount for a in adjustments), ZERO)
    losses_used = min(losses_brought_forward, max(taxable_before, ZERO))
    taxable = taxable_before - losses_used
    losses_cf = losses_brought_forward - losses_used + max(-taxable_before, ZERO)
    tax_payable = round_money(max(taxable, ZERO) * rules.corporation_tax_rate)

    # Deferred tax (IAS 12): carrying amount less tax base, for the differences this slice covers.
    bs = {key: _sum([l for l in lines if l.fs_line == key]) for key in FS_LINES}
    deferred: list[CompLine] = []
    if allowances or bs["ppe_cost"]:
        carrying = bs["ppe_cost"] + bs["ppe_accumulated_depreciation"]  # accumulated depreciation is a credit
        tax_wdv = sum((c.wdv_carried_forward() for c in allowances), ZERO)
        if not allowances:
            warnings.append("no capital deduction classes entered: property, plant and equipment has a tax base of zero")
        deferred.append(CompLine("Property, plant and equipment: carrying amount less tax written-down value",
                                 carrying - tax_wdv, rules.basis["capital_allowances"]))
    if leases:
        rou = sum((l.rou_carrying_amount for l in leases), ZERO)
        liability = sum((l.liability for l in leases), ZERO)
        deferred.append(CompLine("Leases: right-of-use asset less lease liability (both have a tax base of nil)",
                                 rou - liability, rules.basis["lease"], tuple(l.document_id for l in leases)))
    if losses_cf:
        deferred.append(CompLine("Tax losses carried forward (asset, subject to recoverability)", -losses_cf, rules.basis["losses"]))
    deferred_tax = round_money(sum((d.amount for d in deferred), ZERO) * rules.corporation_tax_rate)

    credit_lines = []
    for kind, label in CREDIT_KINDS.items():
        these = [c for c in credits if c.kind == kind]
        if these:
            sources = tuple(f"credit:{i}" for i, c in enumerate(credits) if c.kind == kind)
            basis = rules.basis["other_credit" if kind == "other" else kind]
            credit_lines.append(CompLine(label, -sum((c.amount for c in these), ZERO), basis, sources))
    tax_due = tax_payable + sum((c.amount for c in credit_lines), ZERO)

    return Computation(
        rules_version=rules.version,
        profit_before_tax=profit_before_tax,
        adjustments=tuple(adjustments),
        taxable_before_losses=taxable_before,
        losses_brought_forward=losses_brought_forward,
        losses_used=losses_used,
        losses_carried_forward=losses_cf,
        taxable_profit=taxable,
        rate=rules.corporation_tax_rate,
        tax_payable=tax_payable,
        deferred=tuple(deferred),
        deferred_tax=deferred_tax,
        credits=tuple(credit_lines),
        tax_due=tax_due,
        warnings=tuple(warnings),
    )
