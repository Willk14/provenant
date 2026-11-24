"""The mapping agent: Claude maps trial balance accounts to financial statement lines and tax treatments (ADR 0022).

It works inside the existing rule that nothing suggested is used until a person confirms it. Claude only fills
accounts that are unmapped or that only a keyword rule has guessed; the firm's own confirmed practice and anything a
person has confirmed are never touched. Each answer is checked before it is saved: the account must be one that was
sent, the line and treatment must exist and agree with each other (`check_mapping`), and there must be a reason. The
saved suggestion keeps Claude's reason, so the reviewer sees why.
"""
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from provenant.api import practice_ai
from provenant.api.auth import Principal
from provenant.api.services import Conflict, require
from provenant.api.tax import ANY_ROLE, get_client, mapped_accounts
from provenant.tax.mapping import FIRM_RULE_PREFIX, FS_LINES, TaxTreatment, check_mapping

CLAUDE_RULE_PREFIX = "claude: "
MAX_ACCOUNTS = 400  # accounts sent in one run

TREATMENTS = {
    "taxable": "income charged to tax",
    "exempt": "income not charged to tax, deducted in the computation",
    "deductible": "expense allowed (Income Tax Act s.15)",
    "disallowable": "expense not allowed (s.16), added back",
    "depreciation": "accounting depreciation or amortisation, added back; capital allowances instead",
    "lease_interest": "IFRS 16 lease interest, added back; rentals paid deducted instead",
    "balance_sheet": "not a profit or loss item (every statement of financial position line)",
    "tax_charge": "the income tax expense itself, excluded from profit before tax",
}

SCHEMA = {
    "type": "object",
    "properties": {"mappings": {"type": "array", "maxItems": MAX_ACCOUNTS, "items": {"type": "object", "properties": {
        "code": {"type": "string"},
        "fs_line": {"type": "string", "enum": list(FS_LINES)},
        "tax_treatment": {"type": "string", "enum": [t.value for t in TaxTreatment]},
        "reason": {"type": "string", "description": "One sentence: why this line and treatment."}},
        "required": ["code", "fs_line", "tax_treatment", "reason"]}}},
    "required": ["mappings"],
}


def open_to_claude(rule: str | None, status: str) -> bool:
    """Unmapped accounts and keyword guesses; never the firm's own practice or a person's confirmation."""
    if status == "unmapped":
        return True
    return status == "suggested" and not (rule or "").startswith((FIRM_RULE_PREFIX, CLAUDE_RULE_PREFIX))


def checker(sent: Mapping[str, Any]):
    def check(x: Mapping[str, Any]) -> list[str]:
        problems, seen = [], set()
        for i, m in enumerate(x.get("mappings") or []):
            code = m.get("code")
            if code not in sent:
                problems.append(f"mappings[{i}].code {code!r} was not in the list")
                continue
            if code in seen:
                problems.append(f"account {code} appears twice")
            seen.add(code)
            try:
                check_mapping(m.get("fs_line", ""), m.get("tax_treatment", ""))
            except ValueError as exc:
                problems.append(f"account {code}: {exc}")
            if len(str(m.get("reason", "")).strip()) < 5:
                problems.append(f"account {code}: give a reason")
        return problems

    return check


def prompt(accounts: Sequence[tuple[str, str, str, str]]) -> str:
    lines = "\n".join(f"- {key}: {line.label} ({line.statement.value})" for key, line in FS_LINES.items())
    treatments = "\n".join(f"- {k}: {v}" for k, v in TREATMENTS.items())
    rows = "\n".join(f"{code} | {name} | {balance} | {hint}" for code, name, balance, hint in accounts)
    return (
        "Map each trial balance account of a Kenyan company to one financial statement line and one tax treatment "
        "for the corporation tax computation. Positive balances are debits, negative are credits. Every statement of "
        "financial position line takes balance_sheet; profit or loss lines take one of the others. Map only accounts "
        "you can place with reasonable confidence from the name and balance; leave out the rest for a person. A "
        "person reviews every suggestion before it is used, so say briefly why in each reason.\n\n"
        f"Financial statement lines:\n{lines}\n\nTax treatments:\n{treatments}\n\n"
        f"Accounts (code | name | balance | current keyword guess):\n{rows}"
    )


def map_with_claude(s: Session, p: Principal, client_id: str, year_end: date) -> dict:
    """Ask Claude for the accounts no firm practice or person has settled, save its answers as suggestions."""
    require(p, *ANY_ROLE)
    if not practice_ai.enabled():
        raise Conflict("Claude is off: turn it on for the AI agents to map accounts")
    get_client(s, p.tenant_id, client_id)
    open_accounts = [a for a in mapped_accounts(s, p.tenant_id, client_id, year_end)
                     if open_to_claude(a.mapping.rule, a.mapping.status)][:MAX_ACCOUNTS]
    if not open_accounts:
        return {"sent": 0, "suggested": 0, "left": 0}
    sent = {a.line.code: a for a in open_accounts}
    rows = [(a.line.code, a.line.name, f"{a.balance:.2f}",
             f"{a.mapping.fs_line}/{a.mapping.tax_treatment}" if a.mapping.status == "suggested" else "none")
            for a in open_accounts]
    got = practice_ai._run(practice_ai.factory_of(s), p.tenant_id, "tb_mapping", prompt(rows), SCHEMA,
                           "Submit the mappings.", checker(sent))
    if got is None:
        raise Conflict("Claude could not map the accounts; try again")
    suggested = 0
    for m in got["mappings"]:
        row = sent[m["code"]].mapping
        row.fs_line, row.tax_treatment, row.status = m["fs_line"], m["tax_treatment"], "suggested"
        row.rule = (CLAUDE_RULE_PREFIX + " ".join(m["reason"].split()))[:255]
        suggested += 1
    s.commit()
    return {"sent": len(sent), "suggested": suggested, "left": len(sent) - suggested}

