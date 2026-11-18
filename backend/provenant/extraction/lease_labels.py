"""Deterministic baseline extractor for documents with labelled or conventionally worded terms.

It is the fallback when a model is unavailable and the yardstick a model must beat on the golden set.
It proposes every term it recognises; the service keeps only the terms of the document's kind.
"""
import re
from datetime import datetime

from provenant.extraction.pdftext import open_pages
from provenant.provenance import ExtractedField

_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})"
_RATIO = r"(\d{1,2}(?:\.\d{1,2})?)\s*(?::\s*1|x\b|times)"
_CLAUSE = r"[^.]{0,120}?"  # the rest of the same sentence, lazily

PATTERNS = {
    # credit agreements: the usual "shall not exceed 3.50:1" / "not be less than 4.00:1" wording
    "max_leverage_ratio": rf"leverage ratio{_CLAUSE}(?:shall|must|will)\s+not\s+exceed\s+{_RATIO}",
    "min_interest_cover": rf"interest cover(?:\s+ratio)?{_CLAUSE}(?:shall|must|will)\s+not\s+be\s+less\s+than\s+{_RATIO}",
    "min_liquidity": rf"liquidity{_CLAUSE}(?:shall|must|will)\s+not\s+(?:at\s+any\s+time\s+)?be\s+less\s+than\s+"
    r"(?:[a-z]{3}|[$€£])?\s*(\d[\d,]*(?:\.\d{1,2})?)",
    "first_test_date": rf"first test date\s*[:\-]?\s*{_DATE}",
    "reporting_days": r"within\s+(\d{1,3})\s+days\s+(?:after|of|following)\s+(?:the\s+end\s+of\s+)?each\s+"
    r"(?:financial\s+)?quarter",
    "cure_period_days": rf"(?:cure period|cure right|equity cure){_CLAUSE}(\d{{1,3}})\s+days",
    # leases and amendments
    "commencement_date": r"commencement date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "effective_date": r"effective date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "term_months": r"(?:lease\s+)?term\s*[:\-]?\s*(\d{1,3})\s*months",
    # A colon or a currency marker must sit between label and amount: real leases contain blank forms like
    # "INSERT MONTHLY RENT 2. Acceptance of Premises", where the next number is a section number.
    "monthly_payment": r"monthly\s+(?:rent|payment)\s*(?::\s*(?:[a-z]{3}\b|[$€£])?|(?:[a-z]{3}\b|[$€£]))\s*"
    r"(\d[\d,]*(?:\.\d{1,2})?)",
    "annual_discount_rate": r"(?:discount|incremental borrowing|interest)\s+rate\s*[:\-]?\s*(\d+(?:\.\d+)?\s*%)",
    "payment_timing": r"payable\s+(?:monthly\s+)?in\s+(advance|arrears)",
}
CONFIDENCE = 0.97  # a labelled match; human review still applies to the gate's always-review fields
CONFIDENCE_UNPARSED = 0.5


def normalise(name: str, raw: str) -> tuple[str, bool]:
    """Canonical form of a raw value, and whether it parsed cleanly."""
    raw = " ".join(raw.split())
    if name.endswith("_date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat(), True
            except ValueError:
                pass
        return raw, False
    if name == "annual_discount_rate":
        return raw.replace(" ", ""), True
    if name == "payment_timing":
        return raw.lower(), True
    if name in ("max_leverage_ratio", "min_interest_cover"):
        number = re.match(r"\d{1,2}(?:\.\d{1,2})?", raw)  # "3.50:1", "3.50 times" and "3.50x" are all 3.50x
        return (f"{number.group(0)}x", True) if number else (raw, False)
    if name in ("reporting_days", "cure_period_days"):
        number = re.match(r"\d{1,3}", raw)
        return (number.group(0), True) if number else (raw, False)
    if name == "rent_steps":  # "2026-06-01: $209,393.44" -> "2026-06-01: 209,393.44"
        return re.sub(r"(?i):\s*(?:[a-z]{3}\b|[$€£])\s*", ": ", raw), True
    if name in ("monthly_payment", "min_liquidity"):
        number = re.search(r"\d[\d,]*(?:\.\d{1,2})?", raw)  # drop a currency code or symbol
        return (number.group(0), True) if number else (raw, False)
    return raw, True


class LeaseLabelExtractor:
    name = "labels@2"

    def extract(self, pdf: bytes, document_sha256: str, kind: str = "lease") -> list[ExtractedField]:
        # Patterns are kind-agnostic; the service keeps only the terms of the document's kind.
        found: dict[str, ExtractedField] = {}
        with open_pages(pdf) as pages:
            for page in pages:
                for name, pattern in PATTERNS.items():
                    if name in found:  # first occurrence wins; amendments are handled in M2
                        continue
                    match = re.search(pattern, page.text, re.IGNORECASE)
                    source = match and page.source(document_sha256, *match.span(1))
                    if not source:
                        continue
                    value, clean = normalise(name, match.group(1))
                    found[name] = ExtractedField(
                        name=name,
                        value=value,
                        confidence=CONFIDENCE if clean else CONFIDENCE_UNPARSED,
                        source=source,
                        extractor=self.name,
                    )
        return list(found.values())
