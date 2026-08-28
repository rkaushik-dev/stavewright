from __future__ import annotations

import pytest

from stavewright.finding import Finding, Severity
from stavewright.report.document import (
    Outcome,
    Report,
    Section,
    SectionKind,
    findings_section,
    lines_section,
    table_section,
)


def finding(severity: Severity = Severity.ERROR, rule: str = "r") -> Finding:
    return Finding(rule=rule, severity=severity, message="something")


def test_outcome_exit_codes():
    assert Outcome.PASS.exit_code == 0
    assert Outcome.WARN.exit_code == 0
    assert Outcome.FAIL.exit_code == 1


def test_a_lines_section():
    section = lines_section("Plan", ["one", "two"])
    assert section.kind is SectionKind.LINES
    assert section.lines == ["one", "two"]
    assert not section.is_empty


def test_a_lines_section_serialises():
    assert lines_section("Plan", ["one"]).as_dict() == {
        "heading": "Plan",
        "kind": "lines",
        "lines": ["one"],
    }


def test_a_table_section():
    section = table_section("Steps", ["id", "kind"], [["a", "copy"], ["b", "reg"]])
    assert section.kind is SectionKind.TABLE
    assert section.columns == ["id", "kind"]
    assert len(section.rows) == 2


def test_a_table_section_serialises():
    payload = table_section("Steps", ["id"], [["a"]]).as_dict()
    assert payload == {"heading": "Steps", "kind": "table", "columns": ["id"], "rows": [["a"]]}


def test_a_ragged_table_is_refused():
    with pytest.raises(ValueError, match="row 1 has 1 cells"):
        table_section("Steps", ["id", "kind"], [["a", "copy"], ["b"]])


def test_an_empty_table_is_allowed():
    section = table_section("Steps", ["id"], [])
    assert section.is_empty


def test_a_findings_section_sorts_most_serious_first():
    section = findings_section(
        "Findings",
        [finding(Severity.INFO, "c"), finding(Severity.ERROR, "a"), finding(Severity.WARNING, "b")],
    )
    assert [f.rule for f in section.findings] == ["a", "b", "c"]


def test_a_findings_section_serialises():
    payload = findings_section("Findings", [finding()]).as_dict()
    assert payload["kind"] == "findings"
    assert payload["findings"][0]["rule"] == "r"


def test_a_note_reaches_the_serialised_form():
    assert lines_section("Plan", ["one"], note="a note").as_dict()["note"] == "a note"


def test_no_note_is_left_out():
    assert "note" not in lines_section("Plan", ["one"]).as_dict()


def test_an_empty_section():
    assert Section(heading="Nothing").is_empty


def test_a_report_starts_empty():
    report = Report(title="Commissioning")
    assert report.sections == []
    assert report.outcome is Outcome.PASS
    assert report.all_findings == []


def test_add_chains():
    report = Report(title="x").add(lines_section("a", ["1"])).add(lines_section("b", ["2"]))
    assert [section.heading for section in report.sections] == ["a", "b"]


def test_add_if_skips_empty_sections():
    report = Report(title="x").add_if(lines_section("a", [])).add_if(lines_section("b", ["2"]))
    assert [section.heading for section in report.sections] == ["b"]


def test_all_findings_spans_sections():
    report = (
        Report(title="x")
        .add(findings_section("one", [finding(rule="a")]))
        .add(lines_section("prose", ["ignored"]))
        .add(findings_section("two", [finding(rule="b")]))
    )
    assert [f.rule for f in report.all_findings] == ["a", "b"]


def test_outcome_is_derived_from_the_worst_finding():
    report = Report(title="x").add(findings_section("f", [finding(Severity.ERROR)]))
    assert report.derive_outcome() is Outcome.FAIL
    assert report.outcome is Outcome.FAIL


def test_a_warning_only_report_warns():
    report = Report(title="x").add(findings_section("f", [finding(Severity.WARNING)]))
    assert report.derive_outcome() is Outcome.WARN


def test_an_info_only_report_passes():
    report = Report(title="x").add(findings_section("f", [finding(Severity.INFO)]))
    assert report.derive_outcome() is Outcome.PASS


def test_a_report_with_no_findings_passes():
    assert Report(title="x").derive_outcome() is Outcome.PASS


def test_an_outcome_can_be_set_by_hand():
    report = Report(title="x", outcome=Outcome.FAIL)
    assert report.outcome is Outcome.FAIL
    assert report.all_findings == []


def test_counts_span_the_whole_report():
    report = (
        Report(title="x")
        .add(findings_section("one", [finding(Severity.ERROR), finding(Severity.WARNING)]))
        .add(findings_section("two", [finding(Severity.ERROR)]))
    )
    assert report.counts() == {"error": 2, "warning": 1}


def test_stamping_sets_a_timezone_aware_time():
    report = Report(title="x").stamp()
    assert report.generated_at is not None
    assert report.generated_at.tzinfo is not None


def test_the_serialised_form_carries_the_essentials():
    report = Report(title="Commissioning", subtitle="LINE-PC-04", facts={"plan": "sha256:abc"})
    report.add(lines_section("Plan", ["4 steps"]))
    payload = report.as_dict()
    assert payload["title"] == "Commissioning"
    assert payload["subtitle"] == "LINE-PC-04"
    assert payload["facts"] == {"plan": "sha256:abc"}
    assert payload["outcome"] == "pass"
    assert len(payload["sections"]) == 1


def test_the_serialised_form_leaves_out_what_is_empty():
    payload = Report(title="x").as_dict()
    assert "subtitle" not in payload
    assert "facts" not in payload
    assert "counts" not in payload
    assert "generated_at" not in payload


def test_counts_appear_when_there_are_findings():
    report = Report(title="x").add(findings_section("f", [finding()]))
    assert report.as_dict()["counts"] == {"error": 1}


def test_a_stamped_report_serialises_its_time():
    payload = Report(title="x").stamp().as_dict()
    assert payload["generated_at"].endswith("+00:00")
