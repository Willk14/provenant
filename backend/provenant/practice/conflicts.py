"""Conflict checking: fuzzy matching of a new matter's parties against existing matters and a screening list.

It finds candidates for a person to judge. It never clears or declines a matter by itself.
"""
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

THRESHOLD = 0.85  # below this a name is not shown as a possible match
_DROP = {"ltd", "limited", "plc", "llp", "llc", "inc", "co", "company", "the", "and", "of", "mr", "mrs", "ms", "miss",
         "dr", "hon", "eng", "prof", "rev"}
ADVERSE = {("client", "counterparty"), ("counterparty", "client")}


def normalise(name: str) -> str:
    """Lower case, punctuation and legal suffixes and titles removed, words sorted, so word order does not matter."""
    words = re.sub(r"[^0-9a-z\s]", " ", name.lower().replace("&", " and ")).split()
    return " ".join(sorted(w for w in words if w not in _DROP))


def similarity(a: str, b: str) -> float:
    """1.0 for the same normalised name. A name whose words are all inside the other scores 0.9, provided it has at
    least two words (so "John" does not match every John). Otherwise the character similarity of the two."""
    x, y = normalise(a), normalise(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    small, large = sorted((set(x.split()), set(y.split())), key=len)
    if len(small) >= 2 and small <= large:
        return 0.9
    return round(SequenceMatcher(None, x, y).ratio(), 3)


@dataclass(frozen=True)
class Party:
    name: str
    role: str  # client | counterparty | related | beneficial_owner
    identifier: str = ""  # an ID or registration number, when known


@dataclass(frozen=True)
class Known:
    """A party on an existing matter, or an entry on the firm's screening list (role `watchlist`)."""
    name: str
    role: str
    source: str  # the matter reference, or the list name
    identifier: str = ""


@dataclass(frozen=True)
class Hit:
    party: str
    party_role: str
    matched: str
    matched_role: str
    source: str
    score: float
    kind: str  # adverse | same_side | screening
    reason: str


def _kind(new_role: str, known_role: str) -> str:
    if known_role == "watchlist":
        return "screening"
    return "adverse" if (new_role, known_role) in ADVERSE else "same_side"


def check(parties: Sequence[Party], known: Iterable[Known], threshold: float = THRESHOLD) -> list[Hit]:
    """Every possible match, worst first: screening hits, then adverse parties, then the rest, each by score."""
    hits = []
    for k in known:
        for p in parties:
            same_id = bool(p.identifier and k.identifier and
                           re.sub(r"\W", "", p.identifier).lower() == re.sub(r"\W", "", k.identifier).lower())
            score = 1.0 if same_id else similarity(p.name, k.name)
            if score < threshold:
                continue
            reason = "same ID or registration number" if same_id else (
                "same name" if score == 1.0 else f"similar name ({round(score * 100)}% alike)")
            hits.append(Hit(p.name, p.role, k.name, k.role, k.source, score, _kind(p.role, k.role), reason))
    order = {"screening": 0, "adverse": 1, "same_side": 2}
    return sorted(hits, key=lambda h: (order[h.kind], -h.score, h.party, h.source))
