from __future__ import annotations

import pytest

from stavewright.errors import PreflightError
from stavewright.finding import Finding, Severity
from stavewright.plan.steps import Plan, Step, StepKind
from stavewright.preflight.checks import DEFAULT_CHECKS, Check, PreflightContext
from stavewright.preflight.runner import PreflightReport, describe_checks, run_preflight
from stavewright.target.memory import MemoryTarget


class Noisy(Check):
    name = "noisy"
    title = "always says something"

    def __init__(self, severity: Severity = Severity.WARNING) -> None:
        self.severity = severity

    def check(self, context, target):  # type: ignore[no-untyped-def]
        return [self._finding(self.severity, "noticed something")]


class Quiet(Check):
    name = "quiet"
    title = "never says anything"

    def check(self, context, target):  # type: ignore[no-untyped-def]
        return []


@pytest.fixture
def target():
    return MemoryTarget()


@pytest.fixture
def context(profile, target):
    plan = Plan(
        profile=profile,
        steps=[Step(step_id="s1", kind=StepKind.COPY_FILE, title="copy something")],
    )
    return PreflightContext(plan=plan, profile=profile, facts=target.facts())


def test_a_healthy_terminal_passes_every_default_check(context, target):
    report = run_preflight(context, target)
    assert report.passed
    assert report.findings == []
    assert report.worst is None


def test_every_check_is_recorded_as_having_run(context, target):
    report = run_preflight(context, target)
    assert report.ran == [check.name for check in DEFAULT_CHECKS]


def test_a_warning_does_not_block(context, target):
    report = run_preflight(context, target, checks=(Noisy(Severity.WARNING),))
    assert report.passed
    assert report.worst is Severity.WARNING


def test_an_error_blocks(context, target):
    report = run_preflight(context, target, checks=(Noisy(Severity.ERROR),))
    assert not report.passed
    assert report.worst is Severity.ERROR
    assert len(report.errors) == 1


def test_an_info_does_not_block(context, target):
    report = run_preflight(context, target, checks=(Noisy(Severity.INFO),))
    assert report.passed


def test_raise_if_blocked_is_quiet_when_clear(context, target):
    run_preflight(context, target, checks=(Quiet(),)).raise_if_blocked()


def test_raise_if_blocked_names_the_failing_checks(context, target):
    report = run_preflight(context, target, checks=(Noisy(Severity.ERROR),))
    with pytest.raises(PreflightError, match="stop this run") as excinfo:
        report.raise_if_blocked()
    assert excinfo.value.context["checks"] == ["noisy"]
    assert excinfo.value.context["count"] == 1


def test_a_waived_check_does_not_run(context, target):
    report = run_preflight(
        context, target, checks=(Noisy(Severity.ERROR),), waive={"noisy": "known good bench"}
    )
    assert report.passed
    assert report.ran == []
    assert report.waived == {"noisy": "known good bench"}


def test_waiving_a_check_that_was_not_going_to_run_is_harmless(context, target):
    report = run_preflight(context, target, checks=(Quiet(),), waive={"nothing": "n/a"})
    assert report.ran == ["quiet"]


def test_a_waiver_shows_up_in_the_report_lines(context, target):
    report = run_preflight(
        context, target, checks=(Noisy(Severity.ERROR),), waive={"noisy": "known good bench"}
    )
    assert any("waived noisy: known good bench" in line for line in report.lines())


def test_counts_are_by_severity(context, target):
    checks = (Noisy(Severity.ERROR), Noisy(Severity.WARNING), Noisy(Severity.WARNING))
    report = run_preflight(context, target, checks=checks)
    assert report.counts() == {"error": 1, "warning": 2}


def test_the_first_line_says_whether_it_is_clear(context, target):
    assert run_preflight(context, target, checks=(Quiet(),)).lines()[0] == (
        "preflight clear to proceed: nothing found"
    )


def test_the_first_line_says_when_it_is_blocked(context, target):
    lines = run_preflight(context, target, checks=(Noisy(Severity.ERROR),)).lines()
    assert lines[0] == "preflight blocked: 1 error"


def test_the_first_line_summarises_the_counts(context, target):
    checks = (Noisy(Severity.ERROR), Noisy(Severity.WARNING))
    lines = run_preflight(context, target, checks=checks).lines()
    assert lines[0] == "preflight blocked: 1 error, 1 warning"


def test_lines_put_the_most_serious_finding_first(context, target):
    checks = (Noisy(Severity.INFO), Noisy(Severity.ERROR), Noisy(Severity.WARNING))
    lines = run_preflight(context, target, checks=checks).lines()
    assert lines[1].startswith("[error]")
    assert lines[2].startswith("[warning]")
    assert lines[3].startswith("[info]")


def test_an_empty_report():
    report = PreflightReport()
    assert report.passed
    assert report.errors == []
    assert report.worst is None
    assert report.counts() == {}
    assert report.lines() == ["preflight clear to proceed: nothing found"]


def test_a_report_built_by_hand_still_behaves():
    report = PreflightReport(
        findings=[Finding(rule="r", severity=Severity.ERROR, message="broken")]
    )
    assert not report.passed
    with pytest.raises(PreflightError):
        report.raise_if_blocked()


def test_describe_checks_lists_them_all():
    lines = describe_checks()
    assert len(lines) == len(DEFAULT_CHECKS)
    assert lines[0].count(":") >= 1


def test_describe_checks_takes_a_custom_set():
    assert describe_checks((Quiet(),)) == ["quiet: never says anything"]


def test_a_realistic_blocked_bench(profile, target):
    # Not elevated, no disk space, and a plan that needs both.
    target.is_elevated = False
    target.free_bytes = 1024
    plan = Plan(
        profile=profile,
        steps=[
            Step(step_id="ocx", kind=StepKind.REGISTER_COM, title="register the control"),
        ],
    )
    context = PreflightContext(
        plan=plan, profile=profile, facts=target.facts(), payload_bytes=50_000_000
    )
    report = run_preflight(context, target)
    assert not report.passed
    rules = {finding.rule for finding in report.errors}
    assert rules == {"elevation", "free-space"}


def test_a_realistic_bench_with_only_warnings(profile, target):
    mixed = profile.with_languages(["ru-RU", "tr-TR"])
    plan = Plan(
        profile=mixed,
        steps=[Step(step_id="s1", kind=StepKind.COPY_FILE, title="copy")],
    )
    context = PreflightContext(plan=plan, profile=mixed, facts=target.facts())
    report = run_preflight(context, target)
    assert report.passed
    assert report.worst is Severity.WARNING
    assert {f.rule for f in report.findings} == {"code-page"}
