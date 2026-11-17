"""Score an extractor against the golden set.

    python scripts/evaluate_extractor.py labels
    python scripts/evaluate_extractor.py claude     # calls the Claude API: costs money

A field counts as correct only if the value matches AND it carries a source location. Expected nulls
count as correct when the extractor proposes nothing (inventing a value is worse than leaving a gap).
"""
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fpdf import FPDF

from provenant.extraction import extractor_from_env

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "leases.json"


def make_pdf(pages: list[list[str]]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def canonical(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower().replace(",", "").replace(" ", "")
    try:
        return str(Decimal(text.rstrip("%")).normalize()) + ("%" if text.endswith("%") else "")
    except InvalidOperation:
        return text


def evaluate(extractor) -> tuple[Counter, list[str]]:
    totals, misses = Counter(), []
    for case in json.loads(GOLDEN.read_text()):
        fields = {f.name: f for f in extractor.extract(make_pdf(case["pages"]), "0" * 64)}
        for name, expected in case["expected"].items():
            got = fields.get(name)
            ok = canonical(got.value if got else None) == canonical(expected)
            totals["correct" if ok else "wrong" if got else "missing"] += 1
            if not ok:
                misses.append(f"{case['id']}.{name}: expected {expected!r}, got {got.value if got else None!r}")
    return totals, misses


if __name__ == "__main__":
    import os

    os.environ["PROVENANT_EXTRACTOR"] = sys.argv[1] if len(sys.argv) > 1 else "labels"
    extractor = extractor_from_env()
    totals, misses = evaluate(extractor)
    n = sum(totals.values())
    print(f"{extractor.name}: {totals['correct']}/{n} correct ({totals['correct'] / n:.0%}), "
          f"{totals['wrong']} wrong, {totals['missing']} missing")
    for line in misses:
        print("  -", line)
