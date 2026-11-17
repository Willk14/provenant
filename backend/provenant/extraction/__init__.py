"""Extractors propose fields; they never decide accounting treatment (ADR 0001)."""
from typing import Protocol

from provenant.provenance import ExtractedField


class Extractor(Protocol):
    name: str

    def extract(self, pdf: bytes, document_sha256: str) -> list[ExtractedField]:
        """Return proposed fields, each with a SourceRef into `pdf`. Status must be PROPOSED."""
        ...
