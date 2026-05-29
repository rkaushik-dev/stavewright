from __future__ import annotations

import csv
import io
import json
from xml.etree import ElementTree

import pytest

from v4t_setup.finding import Finding, Severity
from v4t_setup.report.document import (
    Outcome,
    Report,
    findings_section,
    lines_section,
    table_section,
)
from v4t_setup.report.render import (
    RENDERERS,
    render,
    to_csv,
    to_json,
    to_junit,
    to_markdown,
    to_text,
)


def finding(severity=Severity.ERROR, rule="check-digit", **kwargs) -> Finding:
    kwargs.setdefault("message", "check digit does not match the data")
    return Finding(rule=rule, severity=severity, **kwargs)


def sample() -> Report:
    report = Report(
        title="Commissioning report",
        subtitle="LINE-PC-04",
        facts={"plan": "sha256:abc123", "bundle": "S-Jet 600 1.0.0"},
    )
    report.add(lines_section("Plan", ["12 steps", "4 skipped"], note="Built from the manifest."))
    report.add(
        table_section(
            "Steps",
            ["id", "kind", "target"],
            [
                ["file.v4t", "copy-file", r"C:\Sellenis\S-jet600\V4T.exe"],
                ["reg.version", "write-registry", r"HKLM\Software\Matricode\V4T"],
            ],
        )
    )
    report.add(
        findings_section(
            "GS1",
            [
                finding(ai="01", line=2, context={"expected": "6", "found": "1"}),
                finding(Severity.WARNING, "ragged-row", line=24, message="row stops early"),
                finding(Severity.INFO, "note", message="nothing to worry about"),
            ],
        )
    )
    report.derive_outcome()
    return report


def test_the_renderer_table_covers_every_advertised_format():
    assert sorted(RENDERERS) == ["csv", "json", "junit", "markdown", "text"]


def test_render_dispatches():
    report = sample()
    for name in RENDERERS:
        assert render(report, name)


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError, match="unknown report format"):
        render(sample(), "yaml")


def test_text_starts_with_the_title_and_the_outcome():
    text = to_text(sample())
    assert text.splitlines()[0] == "Commissioning report [FAIL]"
    assert text.splitlines()[1] == "LINE-PC-04"


def test_text_shows_a_passing_outcome():
    report = Report(title="Clean", outcome=Outcome.PASS)
    assert to_text(report).splitlines()[0] == "Clean [ok]"


def test_text_shows_a_warning_outcome():
    report = Report(title="Nearly", outcome=Outcome.WARN)
    assert to_text(report).splitlines()[0] == "Nearly [warn]"


def test_text_lists_the_facts_sorted():
    lines = to_text(sample()).splitlines()
    assert "bundle: S-Jet 600 1.0.0" in lines
    assert lines.index("bundle: S-Jet 600 1.0.0") < lines.index("plan: sha256:abc123")


def test_text_summarises_the_counts():
    assert "1 error, 1 warning, 1 info" in to_text(sample())


def test_text_carries_every_heading():
    text = to_text(sample())
    for heading in ("Plan", "Steps", "GS1"):
        assert heading in text


def test_text_carries_a_section_note():
    assert "Built from the manifest." in to_text(sample())


def test_text_renders_a_table_with_a_rule_under_the_header():
    lines = to_text(sample()).splitlines()
    header = next(index for index, line in enumerate(lines) if line.startswith("id "))
    assert set(lines[header + 1].replace(" ", "")) == {"-"}


def test_text_marks_findings_by_severity():
    text = to_text(sample())
    assert "!! check-digit (01) line 2" in text
    assert " ! ragged-row line 24" in text


def test_text_shows_finding_context_sorted():
    lines = to_text(sample()).splitlines()
    expected = next(line for line in lines if "expected:" in line)
    found = next(line for line in lines if "found:" in line)
    assert lines.index(expected) < lines.index(found)


def test_text_has_no_escape_sequences():
    assert "\x1b" not in to_text(sample())


def test_text_ends_with_a_single_newline():
    text = to_text(sample())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_text_wraps_a_long_message():
    long = "x " * 200
    report = Report(title="x").add(findings_section("f", [finding(message=long.strip())]))
    lines = to_text(report, width=60).splitlines()
    assert all(len(line) <= 60 for line in lines)


def test_text_truncates_a_wide_table_cell():
    report = Report(title="x").add(
        table_section("T", ["a", "b"], [["y" * 200, "z" * 200]])
    )
    lines = to_text(report, width=40).splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert "…" in "\n".join(lines)


def test_text_of_an_empty_report():
    assert to_text(Report(title="Nothing")).startswith("Nothing [ok]")


def test_json_round_trips():
    payload = json.loads(to_json(sample()))
    assert payload["title"] == "Commissioning report"
    assert payload["outcome"] == "fail"
    assert payload["counts"] == {"error": 1, "warning": 1, "info": 1}
    assert len(payload["sections"]) == 3


def test_json_keeps_the_table():
    payload = json.loads(to_json(sample()))
    table = next(s for s in payload["sections"] if s["kind"] == "table")
    assert table["columns"] == ["id", "kind", "target"]
    assert len(table["rows"]) == 2


def test_json_keeps_the_findings():
    payload = json.loads(to_json(sample()))
    findings = next(s for s in payload["sections"] if s["kind"] == "findings")
    assert findings["findings"][0]["rule"] == "check-digit"
    assert findings["findings"][0]["context"]["expected"] == "6"


def test_json_is_sorted_so_two_runs_diff_cleanly():
    text = to_json(sample())
    assert text.index('"outcome"') < text.index('"sections"')


def test_json_ends_with_a_newline():
    assert to_json(sample()).endswith("\n")


def test_csv_has_a_header_row():
    rows = list(csv.reader(io.StringIO(to_csv(sample()))))
    assert rows[0] == ["section", "severity", "rule", "subject", "ai", "line", "message"]


def test_csv_holds_only_the_findings():
    rows = list(csv.reader(io.StringIO(to_csv(sample()))))
    assert len(rows) == 4


def test_csv_fills_the_columns():
    rows = list(csv.reader(io.StringIO(to_csv(sample()))))
    assert rows[1] == [
        "GS1",
        "error",
        "check-digit",
        "",
        "01",
        "2",
        "check digit does not match the data",
    ]


def test_csv_leaves_a_missing_line_blank():
    rows = list(csv.reader(io.StringIO(to_csv(sample()))))
    assert rows[3][5] == ""


def test_csv_of_a_report_with_no_findings_is_just_a_header():
    report = Report(title="x").add(lines_section("Plan", ["nothing"]))
    assert len(list(csv.reader(io.StringIO(to_csv(report))))) == 1


def test_junit_is_well_formed():
    root = ElementTree.fromstring(to_junit(sample()))
    assert root.tag == "testsuite"
    assert root.get("name") == "Commissioning report"


def test_junit_counts_tests_and_failures():
    root = ElementTree.fromstring(to_junit(sample()))
    assert root.get("tests") == "3"
    assert root.get("failures") == "1"
    assert root.get("errors") == "0"


def test_junit_makes_a_case_per_finding():
    root = ElementTree.fromstring(to_junit(sample()))
    assert len(root.findall("testcase")) == 3


def test_junit_marks_the_error_as_a_failure():
    root = ElementTree.fromstring(to_junit(sample()))
    failures = root.findall("testcase/failure")
    assert len(failures) == 1
    assert failures[0].get("type") == "check-digit"


def test_junit_puts_a_warning_in_system_out():
    root = ElementTree.fromstring(to_junit(sample()))
    outs = root.findall("testcase/system-out")
    assert len(outs) == 1
    assert "ragged-row" in outs[0].text


def test_junit_of_a_clean_report_still_has_a_case():
    # A suite with no cases reads as "nothing ran" on most build servers.
    report = Report(title="Clean", subtitle="LINE-PC-04")
    root = ElementTree.fromstring(to_junit(report))
    assert root.get("tests") == "1"
    assert root.get("failures") == "0"
    cases = root.findall("testcase")
    assert len(cases) == 1
    assert cases[0].get("name") == "LINE-PC-04"


def test_junit_falls_back_to_a_case_name():
    root = ElementTree.fromstring(to_junit(Report(title="Clean")))
    assert root.findall("testcase")[0].get("name") == "checks"


def test_junit_carries_a_timestamp_when_stamped():
    root = ElementTree.fromstring(to_junit(sample().stamp()))
    assert root.get("timestamp")


def test_junit_omits_the_timestamp_when_not_stamped():
    assert ElementTree.fromstring(to_junit(sample())).get("timestamp") is None


def test_a_word_longer_than_the_width_goes_on_its_own_line():
    # The hand rolled wrapper appended an over long word to the previous line,
    # so a report carrying a long Windows path ran past the width it was given.
    long_path = "C:" + "\\averylongdirectoryname" * 6 + "\\V4T.exe"
    report = Report(title="x").add(
        findings_section("f", [finding(message=f"cannot read {long_path}")])
    )
    lines = to_text(report, width=60).splitlines()
    assert all(len(line) <= 60 for line in lines), max(lines, key=len)


def test_wrapping_keeps_every_word():
    message = " ".join(f"word{n}" for n in range(40))
    report = Report(title="x").add(findings_section("f", [finding(message=message)]))
    rendered = to_text(report, width=50)
    for n in range(40):
        assert f"word{n}" in rendered


def test_an_empty_message_does_not_crash():
    report = Report(title="x").add(findings_section("f", [finding(message=" ")]))
    assert to_text(report, width=40)


def test_a_note_is_wrapped_too():
    note = "x " * 100
    report = Report(title="x").add(lines_section("Plan", ["one"], note=note.strip()))
    lines = to_text(report, width=48).splitlines()
    assert all(len(line) <= 48 for line in lines)


def test_markdown_starts_with_the_title_as_a_heading():
    assert to_markdown(sample()).splitlines()[0] == "# Commissioning report"


def test_markdown_carries_the_subtitle_and_outcome():
    lines = to_markdown(sample()).splitlines()
    assert "_LINE-PC-04_" in lines
    assert "**Outcome:** fail" in lines


def test_markdown_lists_the_facts_sorted():
    lines = to_markdown(sample()).splitlines()
    facts = [line for line in lines if line.startswith("- **")]
    assert facts[0].startswith("- **bundle:**")


def test_markdown_makes_a_real_table():
    lines = to_markdown(sample()).splitlines()
    header = next(index for index, line in enumerate(lines) if line.startswith("| id "))
    assert set(lines[header + 1].replace(" ", "").replace("|", "")) == {"-"}
    assert lines[header + 2].startswith("| file.v4t |")


def test_markdown_escapes_the_backslashes_in_a_windows_path():
    text = to_markdown(sample())
    assert r"C:\\Sellenis\\S-jet600\\V4T.exe" in text


def test_markdown_escapes_a_pipe_so_it_does_not_end_the_cell():
    report = Report(title="x").add(table_section("T", ["a"], [["one|two"]]))
    assert r"one\|two" in to_markdown(report)


def test_markdown_flattens_a_newline_in_a_cell():
    report = Report(title="x").add(table_section("T", ["a"], [["one\ntwo"]]))
    assert "| one two |" in to_markdown(report)


def test_markdown_renders_findings_as_a_list():
    text = to_markdown(sample())
    assert "- **error** `check-digit` check digit does not match the data" in text


def test_markdown_renders_a_lines_section_as_a_list():
    assert "- 12 steps" in to_markdown(sample())


def test_markdown_carries_the_section_notes():
    assert "_Built from the manifest._" in to_markdown(sample())


def test_markdown_of_an_empty_report():
    text = to_markdown(Report(title="Nothing"))
    assert text.splitlines()[0] == "# Nothing"
    assert "**Outcome:** pass" in text


def test_markdown_ends_with_one_newline():
    text = to_markdown(sample())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_markdown_has_a_heading_per_section():
    text = to_markdown(sample())
    for heading in ("## Plan", "## Steps", "## GS1"):
        assert heading in text


def test_a_fact_that_is_a_number_renders_as_one():
    report = Report(title="x", facts={"steps": 12})
    assert "steps: 12" in to_text(report)


def test_a_fact_that_is_none_renders_as_blank():
    # Not the word None. A fact nobody could establish reads as blank and
    # reads as a bug the other way.
    report = Report(title="x", facts={"finished": None})
    assert "finished: \n" in to_text(report) or "finished:" in to_text(report)
    assert "None" not in to_text(report)


def test_a_fact_that_is_a_datetime_is_written_for_a_person():
    from v4t_setup.clock import utc

    report = Report(title="x", facts={"saved": utc(2025, 6, 3, 1, 30)})
    assert "saved: 2025-06-03 01:30:00+00:00" in to_text(report)
    assert "T01:30" not in to_text(report)


def test_the_json_form_stringifies_every_fact():
    report = Report(title="x", facts={"steps": 12, "finished": None})
    payload = json.loads(to_json(report))
    assert payload["facts"] == {"finished": "", "steps": "12"}


def test_the_markdown_form_stringifies_too():
    report = Report(title="x", facts={"steps": 12})
    assert "- **steps:** 12" in to_markdown(report)


def test_fact_lines_are_sorted():
    report = Report(title="x", facts={"zebra": 1, "apple": 2})
    assert list(report.fact_lines()) == ["apple", "zebra"]
