"""Append-only, hash-chained log of human review decisions."""
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from provenant.provenance.model import ExtractedField, Status

_GENESIS = "0" * 64
_ACTIONS = frozenset({Status.APPROVED, Status.CORRECTED, Status.REJECTED})


@dataclass(frozen=True)
class ReviewEvent:
    field_name: str
    document_sha256: str
    reviewer: str
    action: Status
    before: str
    after: str
    at: datetime
    prev_hash: str
    hash: str = ""

    def digest(self) -> str:
        body = asdict(self)
        body.pop("hash")
        body["at"] = self.at.isoformat()
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class ReviewLog:
    """In-memory for now. shortcut: persist as an INSERT-only Postgres table in M1."""

    def __init__(self) -> None:
        self._events: list[ReviewEvent] = []

    @property
    def events(self) -> tuple[ReviewEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        proposal: ExtractedField,
        reviewer: str,
        action: Status,
        corrected_value: str | None = None,
        at: datetime | None = None,
    ) -> ExtractedField:
        """Apply a reviewer's decision and log it. Returns the field in its new state."""
        if proposal.status is not Status.NEEDS_REVIEW:
            raise ValueError(f"{proposal.name} is not awaiting review ({proposal.status})")
        if action not in _ACTIONS:
            raise ValueError(f"{action} is not a review action")
        if not reviewer:
            raise ValueError("reviewer is required")
        if (action is Status.CORRECTED) != (corrected_value is not None):
            raise ValueError("a corrected value is required for, and only for, CORRECTED")

        after = proposal.value if corrected_value is None else corrected_value
        event = ReviewEvent(
            field_name=proposal.name,
            document_sha256=proposal.source.document_sha256,
            reviewer=reviewer,
            action=action,
            before=proposal.value,
            after=after,
            at=at or datetime.now(UTC),
            prev_hash=self._events[-1].hash if self._events else _GENESIS,
        )
        self._events.append(replace(event, hash=event.digest()))
        return replace(proposal, value=after, status=action)

    def verify(self) -> bool:
        """True if no event was altered, removed or reordered."""
        prev = _GENESIS
        for event in self._events:
            if event.prev_hash != prev or event.hash != event.digest():
                return False
            prev = event.hash
        return True
