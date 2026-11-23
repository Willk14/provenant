"""What a review looks for. The standard positions are a starting point; a firm replaces them with its own."""
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

PLAYBOOK_VERSION = "playbook-v1"
MAX_POSITION = 1000


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    position: str  # the firm's preferred position, which the contract is compared with
    keywords: tuple[str, ...]  # used only by the baseline reviewer


DEFAULT_TOPICS: tuple[Topic, ...] = (
    Topic("change_of_control", "Change of control",
          "A change of control should need notice only. Flag consent rights, termination rights or mandatory prepayment.",
          ("change of control", "change in control")),
    Topic("assignment", "Assignment",
          "Assignment to affiliates should be free; other assignment should need consent not unreasonably withheld.",
          ("assign", "transfer of this")),
    Topic("termination", "Termination",
          "Termination should need written notice and a remedy period. Flag termination for convenience by one side only.",
          ("terminat",)),
    Topic("indemnity", "Indemnity",
          "Indemnities should be mutual, limited to third-party claims, and exclude the other party's own fault.",
          ("indemn",)),
    Topic("liability_cap", "Limitation of liability",
          "Liability should be capped for both sides. Flag uncapped liability and any exclusion of direct loss.",
          ("limitation of liability", "liability shall not exceed", "aggregate liability", "consequential")),
    Topic("cross_default", "Cross-default",
          "Cross-default should apply only above a stated threshold and only after acceleration.",
          ("cross-default", "cross default")),
    Topic("material_adverse_change", "Material adverse change",
          "A material adverse change should be objective and limited to the ability to perform payment obligations.",
          ("material adverse",)),
    Topic("governing_law", "Governing law and disputes",
          "Governing law and forum should be stated and acceptable to the client.",
          ("governing law", "governed by", "arbitration", "jurisdiction")),
    Topic("ebitda_definition", "EBITDA definition and add-backs",
          "EBITDA should state its add-backs, any cap on them, and how leases are treated.",
          ("ebitda", "add-back", "add back", "frozen gaap")),
)


def topics_with(custom: Mapping[str, str] | None = None) -> tuple[Topic, ...]:
    """The standard topics with the firm's own positions in place of the standard ones."""
    custom = custom or {}
    known = {t.key for t in DEFAULT_TOPICS}
    if unknown := sorted(set(custom) - known):
        raise ValueError(f"unknown playbook topic: {unknown[0]}")
    out = []
    for topic in DEFAULT_TOPICS:
        position = " ".join(custom.get(topic.key, topic.position).split())
        if not 10 <= len(position) <= MAX_POSITION:
            raise ValueError(f"{topic.label}: a position must be 10 to {MAX_POSITION} characters")
        out.append(replace(topic, position=position))
    return tuple(out)


def fingerprint(topics: tuple[Topic, ...]) -> str:
    """Identifies the exact positions a review was run against."""
    body = json.dumps({t.key: t.position for t in topics}, sort_keys=True)
    return hashlib.sha256(f"{PLAYBOOK_VERSION}|{body}".encode()).hexdigest()
