"""Deterministic baseline extractor for leases with labelled terms ("Monthly Rent: ...").

It is the fallback when a model is unavailable and the yardstick a model must beat on the golden set.
"""
import re
from datetime import datetime

from provenant.extraction.pdftext import open_pages
from provenant.provenance import ExtractedField

PATTERNS = {
    "commencement_date": r"commencement date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "effective_date": r"effective date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "term_months": r"(?:lease\s+)?term\s*[:\-]?\s*(\d{1,3})\s*months",
    "monthly_payment": r"monthly\s+(?:rent|payment)\s*[:\-]?\s*(?:[a-z]{3}|[$€£])?\s*(\d[\d,]*(?:\.\d{1,2})?)",
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
    return raw, True


class LeaseLabelExtractor:
    name = "lease-labels@1"

    def extract(self, pdf: bytes, document_sha256: str) -> list[ExtractedField]:
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
