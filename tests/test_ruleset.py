from __future__ import annotations

import pytest

from stavewright.finding import Finding, Severity
from stavewright.ruleset import (
    Rule,
    describe_rules,
    duplicate_names,
    rule_names,
    run_rules,
)


class Always(Rule[str, int]):
    name = "always"
    title = "always says something"

    def check(self, subject: str, threshold: int, /) -> list[Finding]:
        return [self._finding(Severity.WARNING, f"saw {subject}", value=threshold)]


class Never(Rule[str, int]):
    name = "never"

    def check(self, subject: str, threshold: int, /) -> list[Finding]:
        return []


class OverLimit(Rule[str, int]):
    name = "over-limit"
    title = "subject is longer than the threshold"

    def check(self, subject: str, threshold: int, /) -> list[Finding]:
        if len(subject) <= threshold:
            return []
        return [
            self._finding(
                Severity.ERROR,
                "subject is too long",
                subject=subject,
                length=len(subject),
                limit=threshold,
            )
        ]


def test_a_rule_stamps_its_own_name():
    finding = Always().check("a terminal", 3)[0]
    assert finding.rule == "always"
    assert finding.severity is Severity.WARNING
    assert finding.context == {"value": 3}


def test_every_optional_field_is_available():
    finding = OverLimit().check("a very long subject", 3)[0]
    assert finding.subject == "a very long subject"
    assert finding.context["length"] == 19


def test_the_base_rule_is_abstract():
    with pytest.raises(NotImplementedError):
        Rule().check("x", 1)


def test_repr_names_the_class_and_the_rule():
    assert repr(Always()) == "<Always always>"


def test_run_rules_keeps_the_order_it_was_given():
    findings = run_rules((Always(), OverLimit()), "a very long subject", 3)
    assert [f.rule for f in findings] == ["always", "over-limit"]


def test_run_rules_of_nothing():
    assert run_rules((), "x", 1) == []


def test_a_rule_that_finds_nothing_contributes_nothing():
    assert run_rules((Never(),), "x", 1) == []


def test_skipping_a_rule():
    findings = run_rules((Always(), OverLimit()), "a very long subject", 3, skip={"always"})
    assert [f.rule for f in findings] == ["over-limit"]


def test_skipping_a_name_that_is_not_there_is_harmless():
    findings = run_rules((Always(),), "x", 3, skip={"nothing"})
    assert len(findings) == 1


def test_rule_names():
    assert rule_names((Always(), Never(), OverLimit())) == ["always", "never", "over-limit"]


def test_describe_rules_uses_the_title_when_there_is_one():
    assert describe_rules((Always(), Never())) == [
        "always: always says something",
        "never",
    ]


def test_duplicate_names_finds_a_clash():
    class AlsoAlways(Always):
        pass

    assert duplicate_names((Always(), AlsoAlways())) == ["always"]


def test_no_duplicates_in_a_clean_set():
    assert duplicate_names((Always(), Never(), OverLimit())) == []


def test_make_takes_the_context_as_a_dict():
    finding = Always()._make(
        Severity.INFO, "a message", line=7, context={"line": "not the line field"}
    )
    assert finding.line == 7
    assert finding.context == {"line": "not the line field"}


def test_a_subclass_may_name_its_arguments_after_what_they_are():
    # The base declares check(subject, context, /), so a preflight style check
    # can call them whatever reads best without breaking the override.
    class Renamed(Rule[str, int]):
        name = "renamed"

        def check(self, plan: str, machine: int, /) -> list[Finding]:
            return [self._finding(Severity.INFO, f"{plan} on {machine}")]

    assert Renamed().check("a plan", 4)[0].message == "a plan on 4"


# Every rule set in the toolkit now runs on this base.


def test_the_gs1_rules_are_ruleset_rules():
    from stavewright.gs1.validate import DEFAULT_RULES

    assert all(isinstance(rule, Rule) for rule in DEFAULT_RULES)
    assert duplicate_names(DEFAULT_RULES) == []


def test_the_dataset_rules_are_ruleset_rules():
    from stavewright.gs1.datasetlint import DEFAULT_DATASET_RULES

    assert all(isinstance(rule, Rule) for rule in DEFAULT_DATASET_RULES)
    assert duplicate_names(DEFAULT_DATASET_RULES) == []


def test_the_template_rules_are_ruleset_rules():
    from stavewright.templates.linter import DEFAULT_TEMPLATE_RULES

    assert all(isinstance(rule, Rule) for rule in DEFAULT_TEMPLATE_RULES)
    assert duplicate_names(DEFAULT_TEMPLATE_RULES) == []


def test_the_preflight_checks_are_ruleset_rules():
    from stavewright.preflight.checks import DEFAULT_CHECKS

    assert all(isinstance(rule, Rule) for rule in DEFAULT_CHECKS)
    assert duplicate_names(DEFAULT_CHECKS) == []


def test_every_preflight_check_has_a_title():
    from stavewright.preflight.checks import DEFAULT_CHECKS

    assert all(check.title for check in DEFAULT_CHECKS)


def test_describe_checks_goes_through_the_shared_helper():
    from stavewright.preflight.checks import DEFAULT_CHECKS
    from stavewright.preflight.runner import describe_checks

    assert describe_checks() == describe_rules(DEFAULT_CHECKS)
