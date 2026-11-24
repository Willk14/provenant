"""Precedent filling, deadline counting, notice reading and the due diligence matrix, each hand-worked."""
from datetime import date

import pytest

from provenant.practice import deadlines as dl
from provenant.practice import diligence
from provenant.practice import precedents as pr

NDA = """MUTUAL NON-DISCLOSURE AGREEMENT

This Agreement is made between {{client_name}} and {{counterparty_name}} on {{date}}.

[[optional:term]]It lasts for {{term_years}} years.[[/optional]]

Each party keeps the other's information confidential.
"""


def test_a_precedent_is_filled_and_gaps_stay_visible():
    assert pr.fields(NDA) == ["client_name", "counterparty_name", "date", "term_years"]
    assert pr.options(NDA) == ["term"]
    text = pr.render(NDA, {"client_name": "Kilima Analytics Ltd", "counterparty_name": "Baobab Logistics Ltd"}, set())
    assert "between Kilima Analytics Ltd and Baobab Logistics Ltd on [[date]]." in text
    assert "lasts" not in text and pr.unfilled(text) == ["date"]  # optional clause left out
    with_term = pr.render(NDA, {"term_years": "3"}, {"term"})
    assert "It lasts for 3 years." in with_term


def test_malformed_markers_are_refused():
    with pytest.raises(ValueError):
        pr.check("Between {{client_name} and others")
    with pytest.raises(ValueError):
        pr.check("[[optional:x]]unclosed clause")
    pr.check(NDA)


def test_deviations_are_found_by_paragraph_and_ignore_spacing():
    standard = "One.\n\nTwo  words.\n\nThree.\n"
    assert pr.deviations(standard, "One.\n\nTwo\nwords.\n\nThree.\n") == []
    found = pr.deviations(standard, "One.\n\nTwo changed words.\n\nThree.\n\nFour added.\n")
    assert [(d.kind, d.standard, d.edited) for d in found] == [
        ("changed", "Two words.", "Two changed words."), ("added", "", "Four added.")]
    assert [d.kind for d in pr.deviations(standard, "One.\n\nThree.\n")] == ["removed"]


def test_calendar_deadlines_roll_past_weekends_and_holidays():
    judgment = date(2026, 11, 20)  # a Friday
    # 14 days after Friday 20 November is Friday 4 December.
    assert dl.due_date(judgment, dl.Rule("Notice of appeal", "judgment", days=14)) == date(2026, 12, 4)
    # 15 days lands on Saturday 5 December, so it rolls to Monday 7 December.
    assert dl.due_date(judgment, dl.Rule("x", "judgment", days=15)) == date(2026, 12, 7)
    # With Monday 7 December a holiday, it rolls on to Tuesday 8 December.
    assert dl.due_date(judgment, dl.Rule("x", "judgment", days=15), [date(2026, 12, 7)]) == date(2026, 12, 8)
    # Months keep the day of the month, or the month's last day: 31 January + 1 month = 28 February 2027 (a Sunday,
    # so Monday 1 March), and without rolling it stays on the Sunday.
    assert dl.due_date(date(2027, 1, 31), dl.Rule("x", "decree", months=1)) == date(2027, 3, 1)
    assert dl.due_date(date(2027, 1, 31), dl.Rule("x", "decree", months=1, roll_forward=False)) == date(2027, 2, 28)


def test_court_days_skip_weekends_and_holidays():
    # From Friday 20 November, 5 court days: 23, 24, 25, 26, 27 November, so Friday 27th.
    rule = dl.Rule("Reply", "service", days=5, counting="court_days")
    assert dl.due_date(date(2026, 11, 20), rule) == date(2026, 11, 27)
    # With Tuesday 24 November a holiday, the fifth court day is Monday 30 November.
    assert dl.due_date(date(2026, 11, 20), rule, [date(2026, 11, 24)]) == date(2026, 11, 30)


def test_a_notice_is_read_but_only_proposes():
    text = ("REPUBLIC OF KENYA\nIN THE HIGH COURT AT NAIROBI\nHCCOMM No. E123 of 2026\n"
            "TAKE NOTICE that judgment in this matter was delivered on 20th November 2026. "
            "The matter is listed for mention on 15/01/2027.")
    reading = dl.read_notice(text)
    assert reading.case_number == "HCCOMM NO. E123 OF 2026"
    assert reading.event == "judgment" and reading.dates == [date(2026, 11, 20), date(2027, 1, 15)]
    assert reading.evidence["2026-11-20"] == "20th November 2026"
    assert dl.read_notice("Nothing useful here").event is None


def test_the_matrix_shows_deviations_silence_and_gaps():
    topics = ["change_of_control", "governing_law"]
    docs = [("d1", "supply.pdf"), ("d2", "lease.pdf"), ("d3", "nda.pdf")]
    reviews = {
        "d1": {"status": "done", "findings": [
            {"topic": "change_of_control", "severity": "low", "title": "Notice only", "quote": "q1", "page": 2},
            {"topic": "change_of_control", "severity": "high", "title": "Termination", "quote": "q2", "page": 3}],
            "not_found": ["governing_law"]},
        "d2": {"status": "running", "findings": [], "not_found": []},
    }
    rows = diligence.build(topics, docs, reviews)
    assert [r.filename for r in rows] == ["supply.pdf", "lease.pdf", "nda.pdf"]  # most deviations first
    supply = rows[0].cells
    assert (supply["change_of_control"].severity, supply["change_of_control"].findings) == ("high", 2)
    assert supply["change_of_control"].deviation and supply["governing_law"].state == "not_found"
    assert rows[1].cells["governing_law"].state == "reviewing" and rows[2].cells["governing_law"].state == "not_reviewed"
