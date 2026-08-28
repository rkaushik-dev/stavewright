from __future__ import annotations

import pytest

from stavewright.finding import Severity
from stavewright.verify.drift import DEFAULT_SEVERITY, DriftKind, DriftRecord, DriftReport


def record(kind: DriftKind, subject: str = "C:\\Sellenis\\S-jet600\\V4T.exe", **kwargs):
    return DriftRecord(kind=kind, subject=subject, **kwargs)


def test_every_drift_kind_has_a_default_severity():
    assert set(DEFAULT_SEVERITY) == set(DriftKind)


@pytest.mark.parametrize(
    "kind",
    [
        DriftKind.MISSING,
        DriftKind.MODIFIED,
        DriftKind.UNREGISTERED,
        DriftKind.WRONG_VALUE,
        DriftKind.WRONG_REVISION,
        DriftKind.NOT_APPLIED,
    ],
)
def test_the_kinds_that_stop_a_terminal_are_errors(kind):
    assert record(kind).is_error


def test_an_extra_file_is_only_information():
    assert not record(DriftKind.EXTRA).is_error
    assert record(DriftKind.EXTRA).effective_severity is Severity.INFO


def test_something_that_could_not_be_checked_is_a_warning():
    assert record(DriftKind.UNCHECKABLE).effective_severity is Severity.WARNING
    assert not record(DriftKind.UNCHECKABLE).is_error


def test_a_severity_can_be_overridden():
    strict = record(DriftKind.EXTRA, severity=Severity.ERROR)
    assert strict.is_error


@pytest.mark.parametrize("kind", list(DriftKind))
def test_every_kind_has_a_message(kind):
    assert record(kind).message()


def test_the_messages_read_as_sentences():
    assert record(DriftKind.MISSING).message() == "expected on the terminal and is not there"
    assert record(DriftKind.MODIFIED).message() == "is there but the content has changed"
    assert record(DriftKind.EXTRA).message() == "is on the terminal and not in the manifest"


def test_str_puts_the_subject_first():
    text = str(record(DriftKind.MISSING, subject="V4T.exe"))
    assert text == "[error] V4T.exe: expected on the terminal and is not there"


def test_a_record_becomes_a_finding():
    finding = record(
        DriftKind.MODIFIED,
        step_id="file.v4t",
        expected="sha256:aaa",
        actual="sha256:bbb",
    ).to_finding()
    assert finding.rule == "drift/modified"
    assert finding.severity is Severity.ERROR
    assert finding.context["expected"] == "sha256:aaa"
    assert finding.context["actual"] == "sha256:bbb"
    assert finding.context["step"] == "file.v4t"


def test_a_finding_leaves_out_what_was_not_set():
    finding = record(DriftKind.MISSING).to_finding()
    assert "expected" not in finding.context
    assert "step" not in finding.context


def test_extra_detail_reaches_the_finding():
    finding = record(DriftKind.MISSING, detail={"size": 4096}).to_finding()
    assert finding.context["size"] == 4096


def _report() -> DriftReport:
    return DriftReport(
        terminal="LINE-PC-04",
        plan_digest="sha256:abc",
        checked=42,
        records=[
            record(DriftKind.MISSING, subject="V4T.exe", step_id="file.v4t"),
            record(DriftKind.EXTRA, subject="notes.txt"),
            record(DriftKind.MODIFIED, subject="qu_imager.dll", step_id="file.imager"),
            record(DriftKind.EXTRA, subject="dump.log"),
        ],
    )


def test_a_report_is_a_sequence():
    report = _report()
    assert len(report) == 4
    assert next(r.subject for r in report) == "V4T.exe"


def test_a_report_with_errors_is_not_clean():
    assert not _report().is_clean
    assert len(_report().errors) == 2


def test_a_report_with_only_extras_is_clean():
    report = DriftReport(records=[record(DriftKind.EXTRA, subject="notes.txt")])
    assert report.is_clean


def test_an_empty_report_is_clean():
    assert DriftReport().is_clean
    assert DriftReport().counts() == {}


def test_of_kind():
    assert len(_report().of_kind(DriftKind.EXTRA)) == 2
    assert _report().of_kind(DriftKind.UNREGISTERED) == []


def test_counts_are_in_kind_order():
    counts = _report().counts()
    assert counts == {"missing": 1, "modified": 1, "extra": 2}
    assert list(counts) == ["missing", "modified", "extra"]


def test_findings_come_back_most_serious_first():
    findings = _report().findings()
    assert findings[0].severity is Severity.ERROR
    assert findings[-1].severity is Severity.INFO


def test_rule_counts_group_by_drift_kind():
    assert _report().rule_counts() == {
        "drift/extra": 2,
        "drift/missing": 1,
        "drift/modified": 1,
    }


def test_lines_say_the_terminal_has_drifted():
    lines = _report().lines()
    assert lines[0] == (
        "LINE-PC-04 has drifted: 1 missing, 1 modified, 2 extra (42 checked)"
    )
    assert len(lines) == 5


def test_lines_for_a_clean_terminal():
    report = DriftReport(terminal="LINE-PC-04", checked=42)
    assert report.lines() == ["LINE-PC-04 matches its manifest (42 checked)"]


def test_lines_without_a_terminal_name():
    assert DriftReport().lines()[0].startswith("terminal matches its manifest")


def test_rows_are_flat_for_a_csv():
    rows = _report().as_rows()
    assert len(rows) == 4
    assert rows[0] == {
        "terminal": "LINE-PC-04",
        "kind": "missing",
        "severity": "error",
        "subject": "V4T.exe",
        "step": "file.v4t",
        "expected": "",
        "actual": "",
    }


def test_rows_leave_the_step_blank_when_there_is_none():
    assert _report().as_rows()[1]["step"] == ""


def test_rows_of_an_empty_report():
    assert DriftReport().as_rows() == []
