"""The three finding helpers a rule can reach for, and what each puts where.

They exist because four rule engines want different things in the third
position. Getting that wrong is silent: a line number lands in the AI field and
the report shows ``(24)`` beside a message about row 24.
"""

from __future__ import annotations

from stavewright.finding import Severity
from stavewright.gs1.datasetlint import DatasetRule
from stavewright.preflight.checks import Check
from stavewright.ruleset import Rule


class Plain(Rule[str, None]):
    name = "plain"


def test_the_base_helper_puts_the_ai_third():
    finding = Plain()._finding(Severity.ERROR, "bad", "01")
    assert finding.ai == "01"
    assert finding.line is None
    assert finding.subject is None


def test_the_base_helper_stamps_the_rule_name():
    assert Plain()._finding(Severity.INFO, "note").rule == "plain"


def test_the_base_helper_takes_extras_as_context():
    finding = Plain()._finding(Severity.ERROR, "bad", expected="6", found="1")
    assert finding.context == {"expected": "6", "found": "1"}


def test_make_takes_the_context_as_a_dict():
    finding = Plain()._make(Severity.WARNING, "hm", context={"ai": "01"})
    assert finding.context == {"ai": "01"}
    assert finding.ai is None


def test_make_with_no_context_gives_an_empty_dict():
    assert Plain()._make(Severity.INFO, "note").context == {}


def test_the_dataset_helper_puts_the_line_third():
    finding = DatasetRule()._at(Severity.WARNING, "row stops early", 24)
    assert finding.line == 24
    assert finding.ai is None


def test_the_dataset_helper_takes_extras_as_context():
    finding = DatasetRule()._at(Severity.WARNING, "row stops early", 24, width=3)
    assert finding.context == {"width": 3}


def test_the_dataset_helper_without_a_line():
    assert DatasetRule()._at(Severity.INFO, "file level note").line is None


def test_the_preflight_helper_puts_the_subject_third():
    finding = Check()._about(Severity.ERROR, "no room", "C:\\")
    assert finding.subject == "C:\\"
    assert finding.ai is None
    assert finding.line is None


def test_the_preflight_helper_takes_extras_as_context():
    finding = Check()._about(Severity.ERROR, "no room", "C:\\", needed=1024)
    assert finding.context == {"needed": 1024}


def test_the_two_helpers_are_not_overrides_of_the_base_one():
    # Which is the whole point. If either were called _finding, mypy would have
    # to be told to look the other way and every call site would go unchecked.
    assert DatasetRule._at is not Rule._finding
    assert Check._about is not Rule._finding
    assert DatasetRule._finding is Rule._finding
    assert Check._finding is Rule._finding


def test_every_helper_produces_a_finding_that_renders():
    from stavewright.report.document import Report, findings_section
    from stavewright.report.render import to_text

    findings = [
        Plain()._finding(Severity.ERROR, "bad", "01"),
        DatasetRule()._at(Severity.WARNING, "row stops early", 24),
        Check()._about(Severity.INFO, "plenty of room", "C:\\"),
    ]
    text = to_text(Report(title="x").add(findings_section("f", findings)))
    assert "(01)" in text
    assert "line 24" in text
