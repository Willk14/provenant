"""Page text with character-level positions, so any span of text maps back to a box on the page."""
import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pdfplumber

from provenant.provenance import SourceRef


@dataclass(frozen=True)
class PageText:
    number: int  # 1-based
    text: str
    chars: tuple[dict | None, ...]  # aligned 1:1 with text; None for whitespace the layout inserted

    def source(self, document_sha256: str, start: int, end: int) -> SourceRef | None:
        """SourceRef for text[start:end], or None if the span covers no printed characters."""
        chars = [c for c in self.chars[start:end] if c]
        if not chars:
            return None
        bbox = (
            round(min(c["x0"] for c in chars), 2),
            round(min(c["top"] for c in chars), 2),
            round(max(c["x1"] for c in chars), 2),
            round(max(c["bottom"] for c in chars), 2),
        )
        return SourceRef(document_sha256, self.number, bbox, start, end)

    def span_in_box(self, bbox: tuple[float, float, float, float]) -> tuple[int, int] | None:
        """The text span whose characters' centres fall inside bbox (x0, top, x1, bottom)."""
        x0, top, x1, bottom = bbox
        inside = [
            i
            for i, c in enumerate(self.chars)
            if c and x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom
        ]
        return (inside[0], inside[-1] + 1) if inside else None


@contextmanager
def open_pages(pdf: bytes) -> Iterator[list[PageText]]:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        pages = []
        for number, page in enumerate(doc.pages, start=1):
            textmap = page._get_textmap()  # private API; pdfplumber is pinned to a minor version for this
            pages.append(PageText(number, textmap.as_string, tuple(c for _, c in textmap.tuples)))
        yield pages
