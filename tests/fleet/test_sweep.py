from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from stavewright.errors import VerifyError
from stavewright.finding import Severity
from stavewright.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from stavewright.fleet.sweep import find_snapshots, sweep
from stavewright.model.directory import DirectoryRow, DirectoryTable
from stavewright.model.manifest import Manifest
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.model.profile import TerminalProfile
from stavewright.plan.planner import build_plan
from stavewright.target.memory import MemoryTarget
from stavewright.verify.snapshot import capture

TERMINALS = ("LINE-PC-04", "LINE-PC-05")


def directories() -> DirectoryTable:
    return DirectoryTable.from_rows(
        [
            DirectoryRow.from_cells("TARGETDIR", "SourceDir", None),
            DirectoryRow.from_cells("APPDIR", "S-jet600", "TARGETDIR"),
        ]
    )


def manifest() -> Manifest:
    return Manifest(
        items=[
            PayloadItem(
                item_id="dir.appdir",
                kind=PayloadKind.DIRECTORY,
                name="S-jet600",
                directory="APPDIR",
            ),
            PayloadItem(
                item_id="file.v4t",
                kind=PayloadKind.FILE,
                name="V4T.exe",
                directory="APPDIR",
                source=PurePosixPath("payload/V4T.exe"),
            ),
        ]
    )


def entry(terminal_id: str, line: str = "export") -> RosterEntry:
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=1 if terminal_id.endswith("04") else 2,
        criticality=Criticality.NORMAL,
        shift_pattern=ShiftPattern.DAY,
    )


def roster(*terminal_ids: str) -> Roster:
    names = terminal_ids or TERMINALS
    return Roster(plant="Wrexham", entries=tuple(entry(name) for name in names))


def write_snapshot(root: Path, terminal: str, *, complete: bool = True) -> Path:
    """A snapshot captured off a machine that is or is not fully commissioned."""
    profile = TerminalProfile(terminal_id=terminal, fpga_revision="PCDC-1.4")
    plan = build_plan(manifest(), profile, directories(), stamp=False).plan
    target = MemoryTarget()
    # The install root comes out of the profile, which puts the product under
    # the vendor directory. Writing to the wrong path here would make every
    # terminal look drifted and the test would still pass for the wrong reason.
    installed = str(profile.install_root)
    target.make_directory(installed)
    if complete:
        target.write_file(f"{installed}\\V4T.exe", b"MZ\x90\x00")
    taken = capture(plan, target)
    path = root / f"{terminal}.json"
    taken.write(path)
    return path


@pytest.fixture
def snapshots(tmp_path) -> Path:
    root = tmp_path / "snapshots"
    root.mkdir()
    return root


def run_sweep(roster_in: Roster, root: Path):
    return sweep(roster_in, manifest(), directories(), find_snapshots(root))


def test_a_plant_with_no_snapshots_at_all(snapshots):
    result = run_sweep(roster(), snapshots)
    assert len(result) == 2
    assert result.counts() == {"clean": 0, "drifted": 0, "unseen": 2}


def test_a_terminal_that_matches_its_bundle(snapshots):
    write_snapshot(snapshots, "LINE-PC-04")
    result = run_sweep(roster("LINE-PC-04"), snapshots)
    assert result.counts()["clean"] == 1
    assert result.entries[0].is_clean


def test_a_terminal_that_has_drifted(snapshots):
    write_snapshot(snapshots, "LINE-PC-04", complete=False)
    result = run_sweep(roster("LINE-PC-04"), snapshots)
    assert result.counts()["drifted"] == 1
    assert not result.entries[0].is_clean


def test_the_ones_nobody_looked_at_are_counted(snapshots):
    write_snapshot(snapshots, "LINE-PC-04")
    result = run_sweep(roster(), snapshots)
    assert [item.terminal_id for item in result.unseen] == ["LINE-PC-05"]


def test_a_mixed_plant(snapshots):
    write_snapshot(snapshots, "LINE-PC-04")
    write_snapshot(snapshots, "LINE-PC-05", complete=False)
    result = run_sweep(roster(), snapshots)
    assert result.counts() == {"clean": 1, "drifted": 1, "unseen": 0}


def test_the_plant_name_travels(snapshots):
    assert run_sweep(roster(), snapshots).plant == "Wrexham"


def test_snapshots_are_keyed_on_what_they_say_not_their_name(snapshots):
    # A file named after the wrong machine is a mistake somebody makes once
    # and it is not one to carry into a plant report.
    path = write_snapshot(snapshots, "LINE-PC-04")
    path.rename(snapshots / "LINE-PC-99.json")
    found = find_snapshots(snapshots)
    assert list(found) == ["LINE-PC-04"]


def test_a_snapshot_for_a_terminal_that_is_not_in_the_roster(snapshots):
    write_snapshot(snapshots, "LINE-PC-04")
    write_snapshot(snapshots, "LINE-PC-77")
    result = run_sweep(roster("LINE-PC-04"), snapshots)
    assert [path.name for path in result.unmatched] == ["LINE-PC-77.json"]


def test_a_file_that_is_not_a_snapshot_is_ignored(snapshots):
    (snapshots / "notes.json").write_text("{}", encoding="utf-8")
    write_snapshot(snapshots, "LINE-PC-04")
    assert list(find_snapshots(snapshots)) == ["LINE-PC-04"]


def test_a_snapshot_directory_that_is_not_there(tmp_path):
    with pytest.raises(VerifyError, match="snapshot directory does not exist"):
        find_snapshots(tmp_path / "nowhere")


def test_an_empty_snapshot_directory(snapshots):
    assert find_snapshots(snapshots) == {}


def test_the_entries_come_back_in_working_order(snapshots):
    result = run_sweep(roster(), snapshots)
    assert [item.terminal_id for item in result] == ["LINE-PC-04", "LINE-PC-05"]


def test_a_drifted_terminal_is_an_error(snapshots):
    write_snapshot(snapshots, "LINE-PC-04", complete=False)
    findings = run_sweep(roster("LINE-PC-04"), snapshots).findings()
    assert findings[0].severity is Severity.ERROR
    assert "no longer matches" in findings[0].message
    assert findings[0].subject == "LINE-PC-04"


def test_a_terminal_nobody_looked_at_is_a_warning(snapshots):
    findings = run_sweep(roster("LINE-PC-04"), snapshots).findings()
    assert findings[0].severity is Severity.WARNING
    assert "nobody has captured a snapshot" in findings[0].message


def test_an_unmatched_snapshot_is_a_warning(snapshots):
    write_snapshot(snapshots, "LINE-PC-77")
    findings = run_sweep(roster("LINE-PC-04"), snapshots).findings()
    assert any("the roster does not have" in finding.message for finding in findings)


def test_every_finding_carries_the_line(snapshots):
    write_snapshot(snapshots, "LINE-PC-04", complete=False)
    findings = run_sweep(roster("LINE-PC-04"), snapshots).findings()
    assert findings[0].context["line"] == "export"


def test_describing_a_clean_terminal(snapshots):
    write_snapshot(snapshots, "LINE-PC-04")
    assert run_sweep(roster("LINE-PC-04"), snapshots).describe() == [
        "LINE-PC-04: matches the bundle"
    ]


def test_describing_one_nobody_looked_at(snapshots):
    assert run_sweep(roster("LINE-PC-04"), snapshots).describe() == ["LINE-PC-04: no snapshot"]


def test_describing_a_drifted_one(snapshots):
    write_snapshot(snapshots, "LINE-PC-04", complete=False)
    assert "differences" in run_sweep(roster("LINE-PC-04"), snapshots).describe()[0]


def test_an_empty_roster_sweeps_to_nothing(snapshots):
    result = sweep(Roster(plant="Nowhere"), manifest(), directories(), {})
    assert len(result) == 0
    assert result.findings() == []
