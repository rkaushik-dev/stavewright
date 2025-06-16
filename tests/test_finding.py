from __future__ import annotations

import pytest

from v4t_setup.finding import (
    SEVERITY_ORDER,
    Finding,
    Severity,
    count_by_rule,
    count_by_severity,
    has_errors,
    only,
    sort_findings,
    worst_severity,
)


def finding(rule: str, severity: Severity = Severity.ERROR, **kwargs) -> Finding:
    kwargs.setdefault("message", "something happened")
    return Finding(rule=rule, severity=severity, **kwargs)


def test_severity_ranks_error_first():
    assert Severity.ERROR.rank < Severity.WARNING.rank < Severity.INFO.rank


def test_severity_order_is_most_serious_first():
    assert SEVERITY_ORDER == (Severity.ERROR, Severity.WARNING, Severity.INFO)


def test_str_of_a_bare_finding():
    assert str(finding("check-digit")) == "[error] check-digit: something happened"


def test_str_with_an_ai():
    assert str(finding("check-digit", ai="01")) == "[error] check-digit (01): something happened"


def test_str_with_a_line():
    assert str(finding("ragged-row", Severity.WARNING, line=42)) == (
        "[warning] ragged-row line 42: something happened"
    )


def test_str_with_a_subject():
    assert str(finding("free-space", subject="C:")) == (
        "[error] free-space [C:]: something happened"
    )


def test_str_with_everything():
    result = str(finding("x", Severity.INFO, ai="01", line=7, subject="LINE-PC-04"))
    assert result == "[info] x (01) line 7 [LINE-PC-04]: something happened"


def test_is_error():
    assert finding("x", Severity.ERROR).is_error
    assert not finding("x", Severity.WARNING).is_error
    assert not finding("x", Severity.INFO).is_error


def test_as_dict_leaves_out_what_was_not_set():
    assert finding("check-digit").as_dict() == {
        "rule": "check-digit",
        "severity": "error",
        "message": "something happened",
    }


def test_as_dict_includes_what_was_set():
    payload = finding(
        "check-digit", ai="01", line=7, subject="row", context={"expected": "6"}
    ).as_dict()
    assert payload["ai"] == "01"
    assert payload["line"] == 7
    assert payload["subject"] == "row"
    assert payload["context"] == {"expected": "6"}


def test_a_line_of_zero_is_kept():
    assert finding("x", line=0).as_dict()["line"] == 0


def test_context_defaults_to_empty():
    assert finding("x").context == {}


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], None),
        ([Severity.INFO], Severity.INFO),
        ([Severity.WARNING, Severity.INFO], Severity.WARNING),
        ([Severity.INFO, Severity.ERROR, Severity.WARNING], Severity.ERROR),
        ([Severity.ERROR], Severity.ERROR),
    ],
)
def test_worst_severity(severities, expected):
    assert worst_severity(finding("r", s) for s in severities) == expected


def test_has_errors():
    assert not has_errors([])
    assert not has_errors([finding("r", Severity.WARNING)])
    assert has_errors([finding("r", Severity.WARNING), finding("r", Severity.ERROR)])


def test_count_by_severity_is_in_severity_order():
    findings = [
        finding("a", Severity.INFO),
        finding("b", Severity.ERROR),
        finding("c", Severity.WARNING),
        finding("d", Severity.ERROR),
    ]
    assert count_by_severity(findings) == {"error": 2, "warning": 1, "info": 1}
    assert list(count_by_severity(findings)) == ["error", "warning", "info"]


def test_count_by_severity_leaves_out_zeroes():
    assert count_by_severity([finding("a", Severity.INFO)]) == {"info": 1}


def test_count_by_severity_of_nothing():
    assert count_by_severity([]) == {}


def test_count_by_rule_is_highest_first():
    findings = [finding("a"), finding("b"), finding("b"), finding("c"), finding("c")]
    counts = count_by_rule(findings)
    assert counts == {"b": 2, "c": 2, "a": 1}
    assert list(counts) == ["b", "c", "a"]


def test_count_by_rule_breaks_ties_alphabetically():
    assert list(count_by_rule([finding("z"), finding("a")])) == ["a", "z"]


def test_count_by_rule_of_nothing():
    assert count_by_rule([]) == {}


def test_sort_puts_errors_first():
    findings = [
        finding("a", Severity.INFO, line=1),
        finding("b", Severity.ERROR, line=9),
        finding("c", Severity.WARNING, line=5),
    ]
    assert [f.rule for f in sort_findings(findings)] == ["b", "c", "a"]


def test_sort_uses_the_line_within_a_severity():
    findings = [
        finding("a", Severity.ERROR, line=9),
        finding("b", Severity.ERROR, line=2),
    ]
    assert [f.rule for f in sort_findings(findings)] == ["b", "a"]


def test_sort_puts_findings_without_a_line_first():
    findings = [
        finding("a", Severity.ERROR, line=2),
        finding("b", Severity.ERROR),
    ]
    assert [f.rule for f in sort_findings(findings)] == ["b", "a"]


def test_sort_falls_back_to_the_rule_name():
    findings = [
        finding("z", Severity.ERROR, line=1),
        finding("a", Severity.ERROR, line=1),
    ]
    assert [f.rule for f in sort_findings(findings)] == ["a", "z"]


def test_sort_of_nothing():
    assert sort_findings([]) == []


def test_only_filters_by_severity():
    findings = [finding("a", Severity.ERROR), finding("b", Severity.WARNING)]
    assert [f.rule for f in only(findings, Severity.ERROR)] == ["a"]
    assert only(findings, Severity.INFO) == []


def test_the_gs1_validator_still_exports_the_shared_names():
    from v4t_setup.gs1 import validate as gs1_validate

    assert gs1_validate.Finding is Finding
    assert gs1_validate.Severity is Severity


def test_the_template_linter_uses_the_same_finding():
    from v4t_setup.templates import linter

    result = linter.StructureRule().check(
        linter.SpfTemplate(blocks=[]), linter.TemplateContext()
    )
    assert all(isinstance(item, Finding) for item in result)


def test_the_dataset_linter_uses_the_same_finding():
    from v4t_setup.gs1.dataset import EXPECTED_COLUMNS, parse_dataset
    from v4t_setup.gs1.datasetlint import lint_dataset

    header = ",".join(EXPECTED_COLUMNS)
    row = "1,0001,B,AB:08,00125682945601,A,(01)00125682945601(21)0001,(01)01001000173250(15)150807"
    findings = lint_dataset(parse_dataset(f"{header}\n{row}\n"))
    assert findings
    assert all(isinstance(item, Finding) for item in findings)
