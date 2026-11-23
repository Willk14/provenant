"""Split contract pages into searchable chunks. Pure and deterministic; each chunk keeps its exact place on the page."""
import re
from collections.abc import Sequence
from dataclasses import dataclass

from provenant.extraction.pdftext import PageText

MAX_CHARS = 1200
MIN_BEFORE_HEADING = 60  # enough that a bare heading stays with its text, while each clause gets its own chunk
OVERLAP = 150
_HEADING = re.compile(r"\s*(?:\d+(?:\.\d+)*[.)]?\s|\([a-z0-9]{1,4}\)\s|(?:article|section|clause|schedule)\s+\w+)", re.I)


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    page: int
    char_start: int
    char_end: int
    text: str
    bbox: tuple[float, float, float, float]


def _word_start(text: str, at: int, limit: int) -> int:
    """The start of the next word at or after `at`, so an overlap never begins inside a word."""
    while at < limit and at > 0 and not text[at - 1].isspace():
        at += 1
    while at < limit and text[at].isspace():
        at += 1
    return at


def _spans(text: str, max_chars: int, overlap: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    end = 0

    def flush() -> None:
        nonlocal start
        if start is not None:
            spans.append((start, end))
            start = None

    for line in re.finditer(r"[^\n]+", text):
        line_start, line_end = line.span()
        if not line.group().strip():
            continue
        if line_end - line_start > max_chars:  # one very long line: cut at spaces, overlapping
            flush()
            at = line_start
            while at < line_end:
                stop = min(at + max_chars, line_end)
                if stop < line_end:
                    space = text.rfind(" ", at + max_chars // 2, stop)
                    stop = space if space > at else stop
                spans.append((at, stop))
                if stop >= line_end:
                    break
                at = max(_word_start(text, stop - overlap, stop), at + 1)
            continue
        if start is not None and _HEADING.match(line.group()) and end - start >= MIN_BEFORE_HEADING:
            flush()
        elif start is not None and line_end - start > max_chars:
            previous = end
            flush()
            start = _word_start(text, max(previous - overlap, spans[-1][0] + 1), line_start)
        if start is None:
            start = line_start
        end = line_end
    flush()
    return spans


def chunk_pages(pages: Sequence[PageText], document_sha256: str, *, max_chars: int = MAX_CHARS,
                overlap: int = OVERLAP) -> list[Chunk]:
    """Chunks never cross a page, so each can be highlighted. A clause running over a page break becomes two chunks."""
    out: list[Chunk] = []
    for page in pages:
        for start, end in _spans(page.text, max_chars, overlap):
            while start < end and page.text[start].isspace():
                start += 1
            while end > start and page.text[end - 1].isspace():
                end -= 1
            source = page.source(document_sha256, start, end) if end > start else None
            if source:
                out.append(Chunk(len(out), page.number, start, end, page.text[start:end], source.bbox))
    return out
