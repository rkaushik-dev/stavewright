from __future__ import annotations

import pytest

from v4t_setup.apply.actions import DirectoryPayload
from v4t_setup.apply.executor import Executor
from v4t_setup.finding import Finding, Severity
from v4t_setup.gs1.dataset import EXPECTED_COLUMNS, parse_dataset
from v4t_setup.gs1.datasetlint import lint_dataset
from v4t_setup.plan.planner import build_plan
from v4t_setup.plan.steps import Plan, Step, StepKind
from v4t_setup.preflight.checks import PreflightContext
from v4t_setup.preflight.runner import run_preflight
from v4t_setup.report.builders import (
    MAX_TABLE_ROWS,
    apply_report,
    drift_report,
    gs1_report,
    plan_report,
    preflight_report,
    stage_report,
    template_report,
)
from v4t_setup.report.document import Outcome, SectionKind
from v4t_setup.report.render import render
from v4t_setup.target.memory import MemoryTarget
from v4t_setup.templates.linter import lint_template
from v4t_setup.templates.spf import read_spf
from v4t_setup.verify.drift import DriftKind, DriftRecord, DriftReport

APPDIR = r"C:\Sellenis\S-jet600"


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "stage"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    return DirectoryPayload(root)


def headings(report):
    return [section.heading for section in report.sections]


def small_plan(profile) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            Step(
                step_id="dir.appdir",
                kind=StepKind.CREATE_DIRECTORY,
                title="make APPDIR",
                target=APPDIR,
            ),
            Step(
                step_id="file.v4t",
                kind=StepKind.COPY_FILE,
                title="copy V4T.exe",
                target=rf"{APPDIR}\V4T.exe",
                source="payload/V4T.exe",
            ),
        ],
    )


def test_a_plan_report_lists_the_steps(bundle_manifest, profile, directory_table):
    result = build_plan(bundle_manifest, profile, directory_table, stamp=False)
    report = plan_report(result)
    assert report.title == "Commissioning plan"
    assert "Steps" in headings(report)
    steps = next(s for s in report.sections if s.heading == "Steps")
    assert steps.columns == ["id", "kind", "target", "reversible"]
    assert len(steps.rows) == len(result.plan)


def test_a_plan_report_lists_what_was_skipped(bundle_manifest, profile, directory_table):
    report = plan_report(build_plan(bundle_manifest, profile, directory_table, stamp=False))
    assert "Not applicable" in headings(report)


def test_a_plan_report_names_the_irreversible_steps(bundle_manifest, profile, directory_table):
    report = plan_report(build_plan(bundle_manifest, profile, directory_table, stamp=False))
    section = next(s for s in report.sections if s.heading == "Cannot be rolled back")
    assert any("fpga.pcdc" in line for line in section.lines)


def test_a_plan_report_always_passes(bundle_manifest, profile, directory_table):
    report = plan_report(build_plan(bundle_manifest, profile, directory_table, stamp=False))
    assert report.outcome is Outcome.PASS


def test_a_plan_report_truncates_a_long_table(profile):
    from v4t_setup.plan.planner import PlanReport

    plan = Plan(
        profile=profile,
        steps=[
            Step(step_id=f"s{n}", kind=StepKind.COPY_FILE, title=f"step {n}")
            for n in range(MAX_TABLE_ROWS + 50)
        ],
    )
    report = plan_report(PlanReport(plan=plan, skipped={}, missing_requirements={}))
    steps = next(s for s in report.sections if s.heading == "Steps")
    assert len(steps.rows) == MAX_TABLE_ROWS
    assert "showing the first" in steps.note


def test_a_clean_preflight_report_passes(profile):
    target = MemoryTarget()
    context = PreflightContext(
        plan=small_plan(profile), profile=profile, facts=target.facts()
    )
    report = preflight_report(run_preflight(context, target), profile.terminal_id)
    assert report.outcome is Outcome.PASS
    assert report.subtitle == "LINE-PC-04"


def test_a_blocked_preflight_report_fails(profile):
    target = MemoryTarget()
    target.free_bytes = 1
    context = PreflightContext(
        plan=small_plan(profile),
        profile=profile,
        facts=target.facts(),
        payload_bytes=10_000_000,
    )
    report = preflight_report(run_preflight(context, target), profile.terminal_id)
    assert report.outcome is Outcome.FAIL
    assert "Findings" in headings(report)


def test_a_preflight_report_lists_waivers(profile):
    target = MemoryTarget()
    context = PreflightContext(
        plan=small_plan(profile), profile=profile, facts=target.facts()
    )
    result = run_preflight(context, target, waive={"free-space": "known good bench"})
    report = preflight_report(result, profile.terminal_id)
    assert "Waived" in headings(report)


def test_an_apply_report_for_a_clean_run(profile, payload, tmp_path):
    plan = small_plan(profile)
    target = MemoryTarget()
    result = Executor(target, payload, tmp_path / "run.jsonl").run(plan, "run0001")
    report = apply_report(result, plan)
    assert report.outcome is Outcome.PASS
    assert "Changed" in headings(report)
    assert report.facts["run"] == "run0001"


def test_an_apply_report_for_a_failed_run(profile, payload, tmp_path):
    plan = small_plan(profile)
    plan.steps.append(
        Step(
            step_id="bad",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        )
    )
    target = MemoryTarget()
    result = Executor(target, payload, tmp_path / "run.jsonl").run(plan, "run0001")
    report = apply_report(result, plan)
    assert report.outcome is Outcome.FAIL
    assert "Failure" in headings(report)
    assert "Rolled back" in headings(report)


def test_an_apply_report_names_a_stalled_rollback(profile, payload, tmp_path):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="fpga",
                kind=StepKind.PROGRAM_FPGA,
                title="program",
                source="payload/V4T.exe",
                detail={"revision": "PCDC-1.4"},
            ),
            Step(
                step_id="bad",
                kind=StepKind.COPY_FILE,
                title="bad",
                target=rf"{APPDIR}\missing.dll",
                source="payload/missing.dll",
            ),
        ],
    )
    result = Executor(MemoryTarget(), payload, tmp_path / "run.jsonl").run(plan, "run0001")
    report = apply_report(result, plan)
    assert "Rollback did not complete" in headings(report)


def test_a_clean_drift_report_says_so():
    report = drift_report(DriftReport(terminal="LINE-PC-04", checked=12))
    assert report.outcome is Outcome.PASS
    section = next(s for s in report.sections if s.heading == "Drift")
    assert section.lines == ["the terminal matches its manifest"]


def test_a_dirty_drift_report_fails():
    result = DriftReport(
        terminal="LINE-PC-04",
        checked=12,
        records=[DriftRecord(kind=DriftKind.MISSING, subject="V4T.exe")],
    )
    report = drift_report(result)
    assert report.outcome is Outcome.FAIL
    section = next(s for s in report.sections if s.heading == "Drift")
    assert section.kind is SectionKind.FINDINGS


def test_a_drift_report_of_only_extras_passes():
    result = DriftReport(
        terminal="LINE-PC-04",
        records=[DriftRecord(kind=DriftKind.EXTRA, subject="notes.txt")],
    )
    assert drift_report(result).outcome is Outcome.PASS


def test_a_stage_report(tmp_path, payload):
    from pathlib import PurePosixPath

    from v4t_setup.model.manifest import Manifest
    from v4t_setup.model.payload import PayloadItem, PayloadKind
    from v4t_setup.stage.stager import Stager

    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="file.v4t",
                kind=PayloadKind.FILE,
                name="V4T.exe",
                directory="APPDIR",
                source=PurePosixPath("payload/V4T.exe"),
            ),
            PayloadItem(item_id="reg", kind=PayloadKind.REGISTRY, name="ProductInformation"),
        ]
    )
    result = Stager(tmp_path / "_staging").stage(manifest, payload.root)
    report = stage_report(result)
    assert report.outcome is Outcome.PASS
    assert "Staged" in headings(report)
    assert "Nothing to stage" in headings(report)


def test_a_gs1_report_of_a_clean_file():
    header = ",".join(EXPECTED_COLUMNS)
    row = (
        "1,0001,B,AB:08,01001000173250,A,"
        "(01)01001000173250(21)0001,(01)01001000173250(15)150807,0100100017325"
    )
    dataset = parse_dataset(f"{header}\n{row}\n")
    from v4t_setup.gs1.validate import MarkingContext

    report = gs1_report(dataset, lint_dataset(dataset, MarkingContext(reference_year=2016)))
    assert report.outcome is Outcome.PASS


def test_a_gs1_report_of_the_shipped_file(request):
    from v4t_setup.gs1.dataset import read_dataset
    from v4t_setup.gs1.validate import MarkingContext

    path = request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "GS1-1.txt"
    dataset = read_dataset(path)
    findings = lint_dataset(dataset, MarkingContext(reference_year=2016))
    report = gs1_report(dataset, findings)
    assert report.outcome is Outcome.FAIL
    assert report.facts["rows"] == "130"
    assert report.facts["ragged"] == "32"


def test_a_template_report(request):
    path = request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "T1.spf"
    template = read_spf(path)
    report = template_report(template, lint_template(template))
    assert "Blocks" in headings(report)
    blocks = next(s for s in report.sections if s.heading == "Blocks")
    assert len(blocks.rows) == 4
    assert report.outcome is Outcome.WARN


def test_a_template_report_with_nothing_wrong():
    from v4t_setup.templates.spf import SpfBlock, SpfTemplate

    template = SpfTemplate(
        blocks=[
            SpfBlock(name="TEMPLATE", offset=0),
            SpfBlock(name="TEXTLABEL", offset=10),
        ],
        size=64,
    )
    assert template_report(template, []).outcome is Outcome.PASS


@pytest.mark.parametrize("fmt", ["text", "json", "csv", "junit"])
def test_every_builder_renders_in_every_format(bundle_manifest, profile, directory_table, fmt):
    report = plan_report(build_plan(bundle_manifest, profile, directory_table, stamp=False))
    assert render(report, fmt)


def test_a_finding_built_by_hand_still_renders():
    report = gs1_report(
        parse_dataset(",".join(EXPECTED_COLUMNS) + "\n"),
        [Finding(rule="r", severity=Severity.WARNING, message="hand made")],
    )
    assert report.outcome is Outcome.WARN
    assert "hand made" in render(report, "text")
