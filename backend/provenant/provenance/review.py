"""Append-only, hash-chained log of human review decisions."""
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from provenant.provenance.model import ExtractedField, Status

GENESIS = "0" * 64
_ACTIONS = frozenset({Status.APPROVED, Status.CORRECTED, Status.REJECTED})


def _utc(at: datetime) -> datetime:
    """Databases hand timestamps back naive (SQLite) or in the session zone (Postgres); hash in UTC."""
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


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
        body["at"] = _utc(self.at).isoformat()
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def review(
    proposal: ExtractedField,
    reviewer: str,
    action: Status,
    corrected_value: str | None = None,
    *,
    prev_hash: str = GENESIS,
    at: datetime | None = None,
) -> tuple[ExtractedField, ReviewEvent]:
    """Apply a reviewer's decision. Returns the field in its new state and the event to append."""
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
        at=_utc(at or datetime.now(UTC)),
        prev_hash=prev_hash,
    )
    return replace(proposal, value=after, status=action), replace(event, hash=event.digest())


def verify_chain(events: Iterable[ReviewEvent]) -> bool:
    """True if no event was altered, removed or reordered."""
    prev = GENESIS
    for event in events:
        if event.prev_hash != prev or event.hash != event.digest():
            return False
        prev = event.hash
    return True


class ReviewLog:
    """In-memory chain, used by tests and scripts. The API persists the same events in `review_events`."""

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
        prev = self._events[-1].hash if self._events else GENESIS
        field, event = review(proposal, reviewer, action, corrected_value, prev_hash=prev, at=at)
        self._events.append(event)
        return field

    def verify(self) -> bool:
        return verify_chain(self._events)
