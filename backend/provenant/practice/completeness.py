"""File completeness: what a matter's file must hold before and while the firm acts (ADR 0018).

The required items are firm data with a version, because what a regulator expects changes. This module only decides
which required items are missing, unverified or expired.
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

REQUIREMENTS_VERSION = "kyc-2026.1"
ITEMS = {
    "engagement_letter": "Signed engagement letter",
    "client_id": "Verified client identity document",
    "beneficial_ownership": "Beneficial ownership record",
    "source_of_funds": "Source of funds evidence",
}
EXPIRY_WARNING = timedelta(days=60)


@dataclass(frozen=True)
class Item:
    key: str
    status: str  # missing | provided | verified
    expires_on: date | None = None


@dataclass(frozen=True)
class Gap:
    item: str
    label: str
    problem: str  # missing | not verified | expired | expires soon
    detail: str


def required(client_kind: str, holds_client_money: bool) -> list[str]:
    """Every matter needs a signed engagement letter and a verified identity. An organisation also needs its
    beneficial owners, and money held for the client needs evidence of where it came from."""
    keys = ["engagement_letter", "client_id"]
    if client_kind == "organisation":
        keys.append("beneficial_ownership")
    if holds_client_money:
        keys.append("source_of_funds")
    return keys


def gaps(required_keys: Sequence[str], items: Iterable[Item], today: date) -> list[Gap]:
    have = {i.key: i for i in items}
    out = []
    for key in required_keys:
        label = ITEMS[key]
        item = have.get(key)
        if item is None or item.status == "missing":
            out.append(Gap(key, label, "missing", f"{label} is not on file."))
        elif item.status != "verified":
            out.append(Gap(key, label, "not verified", f"{label} is on file but nobody has verified it."))
        elif item.expires_on and item.expires_on < today:
            out.append(Gap(key, label, "expired", f"{label} expired on {item.expires_on.isoformat()}."))
        elif item.expires_on and item.expires_on <= today + EXPIRY_WARNING:
            out.append(Gap(key, label, "expires soon", f"{label} expires on {item.expires_on.isoformat()}."))
    return out


@dataclass(frozen=True)
class Certificate:
    holder: str
    expires_on: date


def certificate_gaps(advocates: Iterable[str], certificates: Iterable[Certificate], today: date) -> list[Gap]:
    """Each advocate needs a current practising certificate on file. Only the latest certificate counts."""
    latest: dict[str, date] = {}
    for c in certificates:
        latest[c.holder] = max(latest.get(c.holder, c.expires_on), c.expires_on)
    out = []
    for who in sorted(set(advocates)):
        expiry = latest.get(who)
        label = f"Practising certificate: {who}"
        if expiry is None:
            out.append(Gap("practising_certificate", label, "missing", f"No practising certificate on file for {who}."))
        elif expiry < today:
            out.append(Gap("practising_certificate", label, "expired", f"{who}'s certificate expired on {expiry}."))
        elif expiry <= today + EXPIRY_WARNING:
            out.append(Gap("practising_certificate", label, "expires soon", f"{who}'s certificate expires on {expiry}."))
    return out
