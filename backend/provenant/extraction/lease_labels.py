"""Deterministic baseline extractor for leases with labelled terms ("Monthly Rent: ...").

It is the fallback when a model is unavailable and the yardstick a model must beat on the golden set.
"""
import io
import re
from datetime import datetime

import pdfplumber

from provenant.provenance import ExtractedField, SourceRef

PATTERNS = {
    "commencement_date": r"commencement date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "term_months": r"(?:lease\s+)?term\s*[:\-]?\s*(\d{1,3})\s*months",
    "monthly_payment": r"monthly\s+(?:rent|payment)\s*[:\-]?\s*(?:[a-z]{3}|[$€£])?\s*(\d[\d,]*(?:\.\d{1,2})?)",
    "annual_discount_rate": r"(?:discount|incremental borrowing|interest)\s+rate\s*[:\-]?\s*(\d+(?:\.\d+)?\s*%)",
    "payment_timing": r"payable\s+(?:monthly\s+)?in\s+(advance|arrears)",
}
CONFIDENCE = 0.97  # a labelled match; human review still applies to the gate's always-review fields
CONFIDENCE_UNPARSED = 0.5


def _normalise(name: str, raw: str) -> tuple[str, float]:
    if name == "commencement_date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return datetime.strptime(" ".join(raw.split()), "%d %B %Y").date().isoformat(), CONFIDENCE
        except ValueError:
            return raw, CONFIDENCE_UNPARSED
    if name == "annual_discount_rate":
        return raw.replace(" ", ""), CONFIDENCE
    if name == "payment_timing":
        return raw.lower(), CONFIDENCE
    return raw, CONFIDENCE


class LeaseLabelExtractor:
    name = "lease-labels@1"

    def extract(self, pdf: bytes, document_sha256: str) -> list[ExtractedField]:
        found: dict[str, ExtractedField] = {}
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            for page_number, page in enumerate(doc.pages, start=1):
                textmap = page._get_textmap()  # string aligned 1:1 with (char, char_dict | None)
                for name, pattern in PATTERNS.items():
                    if name in found:  # first occurrence wins; amendments are handled in M2
                        continue
                    match = re.search(pattern, textmap.as_string, re.IGNORECASE)
                    if not match:
                        continue
                    start, end = match.span(1)
                    chars = [c for _, c in textmap.tuples[start:end] if c]
                    value, confidence = _normalise(name, match.group(1))
                    found[name] = ExtractedField(
                        name=name,
                        value=value,
                        confidence=confidence,
                        source=SourceRef(
                            document_sha256,
                            page_number,
                            (
                                round(min(c["x0"] for c in chars), 2),
                                round(min(c["top"] for c in chars), 2),
                                round(max(c["x1"] for c in chars), 2),
                                round(max(c["bottom"] for c in chars), 2),
                            ),
                            start,
                            end,
                        ),
                        extractor=self.name,
                    )
        return list(found.values())
