"""The shared finding maker, and that the modules using it still agree.

Four modules had written the same five line private ``_finding`` before anybody
counted them. The interesting part of this test is not that the helper works,
it is that the modules on it produce the same shapes they did before, because
a refactor that quietly drops a context key is a refactor nobody notices until
a report is missing the one field somebody needed.
"""

from __future__ import annotations

import pytest

from v4t_setup.finding import Finding, Severity, reporter


def test_the_rule_goes_on_every_finding():
    make = reporter("thing")
    assert make(Severity.ERROR, "broken").rule == "thing"
    assert make(Severity.INFO, "fine").rule == "thing"


def test_the_severity_and_message_come_through():
    finding = reporter("thing")(Severity.WARNING, "not quite right")
    assert finding.severity is Severity.WARNING
    assert finding.message == "not quite right"


def test_the_subject_is_the_third_argument():
    finding = reporter("thing")(Severity.ERROR, "broken", "APPDIR")
    assert finding.subject == "APPDIR"


def test_the_subject_is_optional():
    assert reporter("thing")(Severity.ERROR, "broken").subject is None


def test_keywords_become_context():
    finding = reporter("thing")(Severity.ERROR, "broken", "x", expected=6, found=1)
    assert finding.context == {"expected": 6, "found": 1}


def test_no_keywords_is_an_empty_context():
    assert reporter("thing")(Severity.ERROR, "broken").context == {}


def test_context_given_once_lands_on_every_finding():
    make = reporter("thing", label="pallet")
    assert make(Severity.ERROR, "a").context == {"label": "pallet"}
    assert make(Severity.INFO, "b", "x", count=2).context == {"label": "pallet", "count": 2}


def test_a_call_site_can_override_the_standing_context():
    make = reporter("thing", label="pallet")
    assert make(Severity.ERROR, "a", label="case").context == {"label": "case"}


def test_two_reporters_do_not_share_state():
    first = reporter("one", a=1)
    second = reporter("two")
    assert first(Severity.INFO, "x").context == {"a": 1}
    assert second(Severity.INFO, "x").context == {}


def test_a_finding_it_makes_is_an_ordinary_finding():
    finding = reporter("thing")(Severity.ERROR, "broken", "x")
    assert isinstance(finding, Finding)
    assert finding.ai is None
    assert finding.line is None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("v4t_setup.environment", "environment"),
        ("v4t_setup.msi.lint", "msi"),
        ("v4t_setup.templates.pack", "template-set"),
        ("v4t_setup.gs1.label", "label"),
    ],
)
def test_every_module_on_the_shared_helper_still_names_its_rule(module, name):
    import importlib

    loaded = importlib.import_module(module)
    declared = getattr(loaded, "RULE", None) or getattr(loaded, "SET_RULE", None)
    assert declared == name


def test_the_environment_findings_keep_their_context():
    from dataclasses import replace

    from v4t_setup.environment import check_environment, collect

    broken = replace(collect(), python_version="3.8.0")
    finding = next(f for f in check_environment(broken) if f.subject == "python")
    assert finding.context["found"] == "3.8.0"
    assert "wanted" in finding.context


def test_the_msi_lint_findings_keep_their_context():
    from v4t_setup.msi.lint import check_properties
    from v4t_setup.msi.tables import MsiDatabase, StringPool

    empty = MsiDatabase(pool=StringPool(codepage=1252, strings=("",), bytes_per_ref=2))
    findings = check_properties(empty)
    assert {finding.rule for finding in findings} == {"msi"}
    assert all(finding.subject for finding in findings)


def test_the_template_set_findings_keep_their_context(tmp_path):
    from v4t_setup.templates.pack import TemplateSet, lint_template_set

    findings = lint_template_set(TemplateSet(root=tmp_path))
    assert findings[0].rule == "template-set"
    assert findings[0].subject == tmp_path.name


def test_no_module_builds_a_finding_by_hand_where_a_reporter_would_do():
    """A rule name written at a construction site is a rule name that drifts.

    Not every Finding( in the source is wrong. The ones this catches are the
    ones with a literal rule= beside them, which is exactly the shape the
    shared helper replaces.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "v4t_setup"
    pattern = re.compile(r"Finding\(\s*\n\s+rule=\"", re.MULTILINE)
    offenders = [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_msi_comparison_findings_keep_their_context():
    from pathlib import Path

    from v4t_setup.aip.normalize import normalise
    from v4t_setup.aip.reader import read_aip
    from v4t_setup.msi.compare import compare_project
    from v4t_setup.msi.tables import read_msi

    root = Path(__file__).resolve().parent.parent
    project = normalise(read_aip(root / "setup.aip"))
    database = read_msi(root / "msi-quattro" / "msi-Jet600.msi")
    findings = compare_project(project, database)
    upgrade = next(f for f in findings if f.subject == "UpgradeCode")
    assert upgrade.rule == "msi-vs-aip"
    assert upgrade.context["project"] and upgrade.context["msi"]

    missing = next(f for f in findings if "sample" in f.context)
    assert isinstance(missing.context["sample"], list)


def test_the_fleet_report_findings_keep_their_context():
    from datetime import date

    from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
    from v4t_setup.fleet.status import plant_status
    from v4t_setup.model.profile import TerminalProfile
    from v4t_setup.report.builders.fleet import status_report
    from v4t_setup.verify.history import History

    entry = RosterEntry(
        terminal_id="A-01",
        profile=TerminalProfile(terminal_id="A-01", fpga_revision="PCDC-1.4"),
        line="one",
        position=1,
        criticality=Criticality.NORMAL,
        shift_pattern=ShiftPattern.DAY,
        commissioned=date(2024, 1, 1),
    )
    report = status_report(plant_status(Roster(plant="W", entries=(entry,)), History()))
    findings = next(part for part in report.sections if part.heading == "Findings").findings
    assert findings[0].rule == "standing"
    assert findings[0].context["line"] == "one"
    assert findings[0].context["standing"] == "disputed"


def test_the_label_findings_keep_the_label_kind():
    from v4t_setup.gs1.label import LabelKind, LabelSpec, check_label
    from v4t_setup.gs1.parse import parse

    findings = check_label(
        LabelSpec(kind=LabelKind.PALLET, string=parse("(02)01001000173250"))
    )
    assert findings
    assert all(finding.context["label"] == "pallet" for finding in findings)
