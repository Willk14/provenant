"""Real-document golden set: public EDGAR exhibits, labelled by hand.

    python scripts/golden_real.py fetch          # download + convert every manifest entry (cached)
    python scripts/golden_real.py show <id> <regex>   # grep a cached document while labelling

Only the manifest (URLs, source hashes, expected answers, notes) is committed. The documents themselves are
cached under golden/real/cache/ (gitignored) and re-fetched on demand. EDGAR exhibits are HTML; each is
reduced to text and rendered to a plain PDF, so the extraction pipeline sees real contract wording.
Downloaded files are untrusted data: they are parsed as text only, never executed.
"""
import hashlib
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent / "golden" / "real"
MANIFEST = ROOT / "manifest.json"
CACHE = ROOT / "cache"
# SEC asks automated clients to identify themselves; this names the tool without personal details.
HEADERS = {"User-Agent": "Provenant golden-set builder research-bot"}
UNICODE_FONT = Path("C:/Windows/Fonts/arial.ttf")


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "center"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _decode(raw: bytes) -> str:
    """EDGAR exhibits are often Windows-1252 (smart quotes, section signs) despite no declaration."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def html_to_text(raw: bytes) -> str:
    parser = _Text()
    parser.feed(_decode(raw))
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def text_to_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, margin=18)
    if UNICODE_FONT.exists():
        pdf.add_font("body", "", str(UNICODE_FONT))
        pdf.set_font("body", size=9)
    else:  # core fonts are Latin-1 only
        pdf.set_font("Helvetica", size=9)
        text = text.encode("latin-1", errors="replace").decode("latin-1")
    pdf.add_page()
    for paragraph in text.split("\n"):
        pdf.multi_cell(0, 4.5, paragraph or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def fetch(entry: dict) -> tuple[Path, Path]:
    """Cached HTML and PDF for an entry. Verifies the source hash recorded in the manifest."""
    CACHE.mkdir(parents=True, exist_ok=True)
    source, pdf = CACHE / f"{entry['id']}.htm", CACHE / f"{entry['id']}.pdf"
    if not source.exists():
        r = httpx.get(entry["url"], headers=HEADERS, timeout=60, follow_redirects=True)
        r.raise_for_status()
        source.write_bytes(r.content)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if entry.get("source_sha256") and entry["source_sha256"] != digest:
        raise ValueError(f"{entry['id']}: source changed since labelling ({digest})")
    if not pdf.exists():
        pdf.write_bytes(text_to_pdf(html_to_text(source.read_bytes())))
    return source, pdf


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if command == "fetch":
        for entry in manifest():
            source, pdf = fetch(entry)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            print(f"{entry['id']:<28} {entry['kind']:<17} {len(source.read_bytes()) // 1024:>5} KB  sha256 {digest[:12]}")
    elif command == "show":
        entry = next(e for e in manifest() if e["id"] == sys.argv[2])
        text = html_to_text(fetch(entry)[0].read_bytes())
        for m in re.finditer(sys.argv[3], text, re.IGNORECASE):
            print("...", text[max(0, m.start() - 300): m.end() + 400].replace("\n", " "), "...\n")


if __name__ == "__main__":
    main()
