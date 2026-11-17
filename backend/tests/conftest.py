import pytest

from provenant.provenance import ExtractedField, SourceRef

DOC = "a" * 64


@pytest.fixture
def make_field():
    def _make(name: str, value: str, confidence: float = 0.99) -> ExtractedField:
        return ExtractedField(
            name=name,
            value=value,
            confidence=confidence,
            source=SourceRef(DOC, page=1, bbox=(72.0, 100.0, 300.0, 114.0), char_start=0, char_end=len(value)),
            extractor="test-extractor@0",
        )

    return _make
