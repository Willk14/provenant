from dataclasses import replace

import pytest

from provenant.provenance import ConfidenceGate, ReviewLog, SourceRef, Status

GATE = ConfidenceGate(version="lease-v1", threshold=0.95, always_review=frozenset({"payment_timing"}))


def test_source_ref_validates_location():
    with pytest.raises(ValueError):
        SourceRef("not-a-hash", 1, (0, 0, 1, 1), 0, 1)
    with pytest.raises(ValueError):
        SourceRef("a" * 64, 0, (0, 0, 1, 1), 0, 1)
    with pytest.raises(ValueError):
        SourceRef("a" * 64, 1, (5, 0, 1, 1), 0, 1)


def test_gate_routes_by_confidence_and_risk(make_field):
    assert GATE.route(make_field("monthly_payment", "1000", 0.97)).status is Status.AUTO_ACCEPTED
    assert GATE.route(make_field("monthly_payment", "1000", 0.80)).status is Status.NEEDS_REVIEW
    risky = GATE.route(make_field("payment_timing", "arrears", 0.999))
    assert risky.status is Status.NEEDS_REVIEW and risky.gate_version == "lease-v1"
    with pytest.raises(ValueError):
        GATE.route(risky)


def test_review_log_records_correction_and_detects_tampering(make_field):
    log = ReviewLog()
    pending = GATE.route(make_field("monthly_payment", "1,000", 0.5))
    fixed = log.record(pending, "reviewer@firm", Status.CORRECTED, corrected_value="1,100")
    assert fixed.value == "1,100" and fixed.verified
    log.record(GATE.route(make_field("term_months", "12", 0.5)), "reviewer@firm", Status.APPROVED)

    assert log.verify()
    assert log.events[0].before == "1,000" and log.events[0].after == "1,100"

    log._events[0] = replace(log.events[0], after="9,999")  # simulate an edit behind the API's back
    assert not log.verify()


def test_review_requires_pending_field_and_consistent_action(make_field):
    log = ReviewLog()
    accepted = GATE.route(make_field("monthly_payment", "1000", 0.99))
    with pytest.raises(ValueError):
        log.record(accepted, "r", Status.APPROVED)
    pending = GATE.route(make_field("monthly_payment", "1000", 0.1))
    with pytest.raises(ValueError):
        log.record(pending, "r", Status.CORRECTED)
    with pytest.raises(ValueError):
        log.record(pending, "r", Status.APPROVED, corrected_value="2")
