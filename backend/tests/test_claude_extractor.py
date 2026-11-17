"""The Claude extractor's grounding logic, with a fake client (no network, no cost)."""
from types import SimpleNamespace

import pytest

from provenant.extraction.claude import ClaudeLeaseExtractor, Cited, ExtractionRefused, LeaseExtraction
from tests.test_api import make_pdf

LINES = [
    "THIS LEASE between Riverside Holdings and Acme Ltd.",
    "The term is three (3) years beginning on 1 March 2026.",
    "Rent of USD 2,500 per month is payable in advance.",
]
SHA = "b" * 64


class FakeClient:
    def __init__(self, output: LeaseExtraction | None, stop_reason: str = "end_turn") -> None:
        self.output, self.stop_reason, self.calls = output, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.output, stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="cyber")
        )


def answer(**cited: Cited) -> LeaseExtraction:
    empty = Cited(value=None, page=None, quote=None)
    names = LeaseExtraction.model_fields
    return LeaseExtraction(**{name: cited.get(name, empty) for name in names})


def test_grounded_values_get_a_bbox_and_need_review():
    client = FakeClient(
        answer(
            term_months=Cited(value="36", page=1, quote="three (3) years"),
            monthly_payment=Cited(value="2500", page=1, quote="USD  2,500 per month"),  # whitespace differs
            payment_timing=Cited(value="Advance", page=1, quote="payable in advance"),
        )
    )
    fields = {f.name: f for f in ClaudeLeaseExtractor(client).extract(make_pdf(LINES), SHA)}
    assert {n: f.value for n, f in fields.items()} == {"term_months": "36", "monthly_payment": "2500", "payment_timing": "advance"}
    rent = fields["monthly_payment"]
    assert rent.source.page == 1 and rent.source.bbox[0] < rent.source.bbox[2]
    assert rent.confidence < 0.95  # uncalibrated: below the gate, so a human reviews it
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert "<page number=\"1\">" in call["messages"][0]["content"]


def test_hallucinated_quotes_and_bad_pages_are_dropped():
    client = FakeClient(
        answer(
            annual_discount_rate=Cited(value="8%", page=1, quote="discount rate of 8%"),  # not in document
            term_months=Cited(value="36", page=7, quote="three (3) years"),  # page does not exist
        )
    )
    assert ClaudeLeaseExtractor(client).extract(make_pdf(LINES), SHA) == []


def test_refusal_raises_so_the_job_is_retried():
    with pytest.raises(ExtractionRefused):
        ClaudeLeaseExtractor(FakeClient(None, stop_reason="refusal")).extract(make_pdf(LINES), SHA)


def test_scanned_pdf_without_text_is_not_sent():
    client = FakeClient(answer())
    assert ClaudeLeaseExtractor(client).extract(make_pdf([]), SHA) == []
    assert client.calls == []
