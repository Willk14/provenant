"""Extractors propose fields; they never decide accounting treatment (ADR 0001)."""
import os
from typing import Protocol

from provenant.provenance import ExtractedField


class Extractor(Protocol):
    name: str

    def extract(self, pdf: bytes, document_sha256: str) -> list[ExtractedField]:
        """Return proposed fields, each with a SourceRef into `pdf`. Status must be PROPOSED."""
        ...


def extractor_from_env() -> Extractor:
    """PROVENANT_EXTRACTOR=labels (default) or claude."""
    choice = os.environ.get("PROVENANT_EXTRACTOR", "labels")
    if choice == "claude":
        from provenant.extraction.claude import ClaudeLeaseExtractor

        return ClaudeLeaseExtractor.from_env()
    if choice == "labels":
        from provenant.extraction.lease_labels import LeaseLabelExtractor

        return LeaseLabelExtractor()
    raise ValueError(f"unknown PROVENANT_EXTRACTOR {choice!r}")
