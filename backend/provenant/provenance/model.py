"""Where a fact came from, and whether it may be trusted yet."""
import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SourceRef:
    """An exact location in an ingested document.

    bbox is (x0, top, x1, bottom) in PDF points with a top-left origin, which is what pdf.js
    viewers draw in. char_start/char_end index into that page's extracted text.
    """

    document_sha256: str
    page: int
    bbox: tuple[float, float, float, float]
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.document_sha256):
            raise ValueError("document_sha256 must be 64 lowercase hex chars")
        if self.page < 1:
            raise ValueError("pages are 1-based")
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError("bbox must have positive width and height")
        if not 0 <= self.char_start < self.char_end:
            raise ValueError("char range must be non-empty and non-negative")


class Status(StrEnum):
    PROPOSED = "proposed"
    AUTO_ACCEPTED = "auto_accepted"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"


VERIFIED = frozenset({Status.AUTO_ACCEPTED, Status.APPROVED, Status.CORRECTED})


@dataclass(frozen=True)
class ExtractedField:
    """A value proposed by an extractor. `value` is the raw text; the core parses it."""

    name: str
    value: str
    confidence: float
    source: SourceRef
    extractor: str
    status: Status = Status.PROPOSED
    gate_version: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

    @property
    def verified(self) -> bool:
        return self.status in VERIFIED
