"""Score an extractor against the golden sets.

    python scripts/evaluate_extractor.py labels           # synthetic leases (fast, free)
    python scripts/evaluate_extractor.py labels --real    # real EDGAR documents (golden/real/manifest.json)
    python scripts/evaluate_extractor.py claude --real    # calls the Claude API: costs money

Scoring, per expected term:
- correct:  the value matches (or nothing was proposed where the document states nothing)
- wrong:    a value was proposed but it differs from the label
- invented: a value was proposed where the document states nothing (the most dangerous kind of wrong)
- missing:  the document states a value and nothing was proposed (safe: a person fills the gap)
Every proposed value carries a page + box by construction; values the model could not ground were dropped.
"""
import json
import os
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fpdf import FPDF

from provenant.core.formats import parse_free_periods, parse_rent_steps
from provenant.extraction import extractor_from_env

GOLDEN = Path(__file__).resolve().parent.parent / "golden"


def make_pdf(pages: list[list[str]]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def canonical(value: str | None, name: str = "") -> str | None:
    """Compare meaning, not formatting: 3.5x == 3.50x, 6,410 == 6410.00, 12 % == 12%; schedules compare as
    sorted steps and sets of months, so order and spacing do not matter."""
    if value is None:
        return None
    try:
        if name == "rent_steps":
            return repr(sorted((str(k), canonical(str(v))) for k, v in parse_rent_steps(value)))
        if name == "free_periods":
            return repr(sorted(str(m) for m in parse_free_periods(value)))
    except ValueError:
        return value.strip()
    text = value.strip().lower().replace(",", "").replace(" ", "")
    for suffix in ("%", "x"):
        if text.endswith(suffix):
            try:
                return str(Decimal(text[:-1]).normalize()) + suffix
            except InvalidOperation:
                return text
    try:
        return str(Decimal(text).normalize())
    except InvalidOperation:
        return text


def score(expected: dict, got: dict) -> tuple[Counter, list[str]]:
    totals, misses = Counter(), []
    for name, want in expected.items():
        field = got.get(name)
        value = field.value if field else None
        right = canonical(value, name) == canonical(want, name)
        totals["found"] += want is not None
        totals["found_correct"] += want is not None and right
        totals["proposed"] += value is not None
        totals["proposed_correct"] += value is not None and right
        if right:
            totals["correct"] += 1
            continue
        outcome = "missing" if value is None else ("invented" if want is None else "wrong")
        totals[outcome] += 1
        misses.append(f"{outcome:<8} {name}: expected {want!r}, got {value!r}"
                      + (f" (p.{field.source.page})" if field else ""))
    return totals, misses


def synthetic_cases():
    for case in json.loads((GOLDEN / "leases.json").read_text()):
        yield case["id"], "lease", make_pdf(case["pages"]), case["expected"]


def real_cases():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from golden_real import fetch, manifest

    for entry in manifest():
        _, pdf = fetch(entry)
        yield entry["id"], entry["kind"], pdf.read_bytes(), entry["expected"]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    real = "--real" in sys.argv
    os.environ["PROVENANT_EXTRACTOR"] = args[0] if args else "labels"
    extractor = extractor_from_env()

    totals, report = Counter(), {}
    for case_id, kind, pdf, expected in (real_cases() if real else synthetic_cases()):
        started = time.monotonic()
        got = {f.name: f for f in extractor.extract(pdf, "0" * 64, kind=kind)}
        case_totals, misses = score(expected, got)
        totals.update(case_totals)
        n = sum(case_totals[k] for k in ("correct", "wrong", "invented", "missing"))
        print(f"{case_id:<24} {kind:<17} {case_totals['correct']}/{n} correct  ({time.monotonic() - started:.0f}s)")
        for line in misses:
            print("    -", line)
        report[case_id] = {"kind": kind, **case_totals, "misses": misses}

    n = sum(totals[k] for k in ("correct", "wrong", "invented", "missing"))
    print(
        f"\n{extractor.name}: {totals['correct']}/{n} correct ({totals['correct'] / n:.0%}), "
        f"{totals['wrong']} wrong, {totals['invented']} invented, {totals['missing']} missing"
    )
    # Recall: of the values the documents state, how many were found. Precision: of the values proposed,
    # how many were right. Precision is what makes auto-accepting safe; recall is what saves time.
    stated, proposed = totals["found"], totals["proposed"]
    print(
        f"recall {totals['found_correct']}/{stated} stated values found"
        f" ({totals['found_correct'] / stated:.0%})" if stated else "recall n/a",
        f"| precision {totals['proposed_correct']}/{proposed} proposals right"
        f" ({totals['proposed_correct'] / proposed:.0%})" if proposed else "| precision n/a (nothing proposed)",
    )
    for key in ("found", "found_correct", "proposed", "proposed_correct"):
        totals.pop(key, None)
    if real:
        out = GOLDEN / "real" / f"results-{extractor.name.split(':')[0].split('@')[0]}.json"
        out.write_text(json.dumps({"extractor": extractor.name, "totals": totals, "cases": report}, indent=2))
        print(f"wrote {out.relative_to(GOLDEN.parent)}")


if __name__ == "__main__":
    main()
