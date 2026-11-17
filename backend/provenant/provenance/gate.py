"""Confidence-gate governance: decides which proposals a human must see."""
from dataclasses import dataclass, field, replace

from provenant.provenance.model import ExtractedField, Status


@dataclass(frozen=True)
class ConfidenceGate:
    """`threshold` is tau, calibrated on a labelled validation set; `version` stamps every decision."""

    version: str
    threshold: float
    always_review: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")

    def route(self, proposal: ExtractedField) -> ExtractedField:
        if proposal.status is not Status.PROPOSED:
            raise ValueError(f"{proposal.name} was already routed ({proposal.status})")
        auto = proposal.confidence >= self.threshold and proposal.name not in self.always_review
        return replace(
            proposal,
            status=Status.AUTO_ACCEPTED if auto else Status.NEEDS_REVIEW,
            gate_version=self.version,
        )
