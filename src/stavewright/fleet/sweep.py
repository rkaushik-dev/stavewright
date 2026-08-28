"""Reconciling a plant rather than a machine.

``run verify`` answers whether one terminal still matches its bundle, and it
has to be run standing next to that terminal. A plant has thirty of them and
nobody walks round thirty machines to find out which two have drifted.

So the bench captures a snapshot on each visit, the snapshots come back to the
office in a directory, and this reconciles the lot in one pass. What comes out
is the two lines somebody actually wants: which terminals have drifted, and
which ones nobody has a snapshot for at all.

The second is the one that gets missed. A plant report that lists three
terminals as clean out of thirty, without saying that twenty seven were never
looked at, is worse than no report.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from stavewright.errors import VerifyError
from stavewright.finding import Finding, Severity, reporter
from stavewright.fleet.roster import Roster, RosterEntry
from stavewright.model.directory import DirectoryTable
from stavewright.model.manifest import Manifest
from stavewright.plan.planner import build_plan
from stavewright.verify.drift import DriftReport
from stavewright.verify.reconcile import reconcile
from stavewright.verify.snapshot import Snapshot, read_snapshot

RULE = "sweep"
_finding = reporter(RULE)

SNAPSHOT_GLOB = "*.json"


@dataclass(frozen=True, slots=True)
class SweepEntry:
    """One terminal in a sweep, and what its snapshot said."""

    entry: RosterEntry
    snapshot_path: Path | None = None
    drift: DriftReport | None = None
    unreadable: str = ""

    @property
    def terminal_id(self) -> str:
        return self.entry.terminal_id

    @property
    def was_looked_at(self) -> bool:
        return self.drift is not None

    @property
    def is_clean(self) -> bool:
        return self.drift is not None and self.drift.is_clean

    def describe(self) -> str:
        if self.unreadable:
            return f"{self.terminal_id}: snapshot could not be read, {self.unreadable}"
        if self.drift is None:
            return f"{self.terminal_id}: no snapshot"
        if self.drift.is_clean:
            return f"{self.terminal_id}: matches the bundle"
        return f"{self.terminal_id}: {len(self.drift)} differences"


@dataclass(frozen=True, slots=True)
class Sweep:
    """A whole plant reconciled from snapshots."""

    plant: str
    entries: tuple[SweepEntry, ...] = ()
    unmatched: tuple[Path, ...] = field(default=())
    """Snapshots whose terminal is not in the roster. Usually a machine that
    was decommissioned, occasionally a snapshot from the wrong plant."""

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[SweepEntry]:
        return iter(self.entries)

    @property
    def drifted(self) -> list[SweepEntry]:
        return [entry for entry in self.entries if entry.was_looked_at and not entry.is_clean]

    @property
    def clean(self) -> list[SweepEntry]:
        return [entry for entry in self.entries if entry.is_clean]

    @property
    def unseen(self) -> list[SweepEntry]:
        """Terminals with no readable snapshot. The half people forget."""
        return [entry for entry in self.entries if not entry.was_looked_at]

    def counts(self) -> dict[str, int]:
        return {
            "clean": len(self.clean),
            "drifted": len(self.drifted),
            "unseen": len(self.unseen),
        }

    def findings(self) -> list[Finding]:
        findings = [
            _finding(
                Severity.ERROR,
                "this terminal no longer matches the bundle it was commissioned from",
                entry.terminal_id,
                differences=len(entry.drift) if entry.drift else 0,
                line=entry.entry.line,
            )
            for entry in self.drifted
        ]
        findings.extend(
            _finding(
                Severity.WARNING,
                entry.unreadable or "nobody has captured a snapshot of this terminal",
                entry.terminal_id,
                line=entry.entry.line,
            )
            for entry in self.unseen
        )
        findings.extend(
            _finding(
                Severity.WARNING,
                "this snapshot is for a terminal the roster does not have",
                path.name,
            )
            for path in self.unmatched
        )
        return findings

    def describe(self) -> list[str]:
        return [entry.describe() for entry in self.entries]


def find_snapshots(root: Path) -> dict[str, Path]:
    """Every snapshot in *root*, keyed by the terminal it came from.

    Keyed by what the snapshot says rather than by its file name. A file called
    ``LINE-PC-04.json`` that holds a snapshot of another machine is a mistake
    somebody makes once, and it is not one to propagate into a plant report.

    :raises VerifyError: when the directory does not exist.
    """
    if not root.is_dir():
        raise VerifyError("snapshot directory does not exist", context={"path": str(root)})
    found: dict[str, Path] = {}
    for path in sorted(root.glob(SNAPSHOT_GLOB)):
        try:
            snapshot = read_snapshot(path)
        except VerifyError:
            continue
        if snapshot.terminal:
            found[snapshot.terminal] = path
    return found


def sweep(
    roster: Roster,
    manifest: Manifest,
    directories: DirectoryTable,
    snapshots: dict[str, Path],
) -> Sweep:
    """Reconcile every terminal in *roster* against *manifest*.

    Each terminal gets its own plan, because the profile is what makes one
    bundle resolve to different work, and reconciling a Mini against a plan
    built for an S-Jet 600 would report the missing bitstream as drift.
    """
    entries: list[SweepEntry] = []
    for entry in roster.in_working_order():
        path = snapshots.get(entry.terminal_id)
        if path is None:
            entries.append(SweepEntry(entry=entry))
            continue
        try:
            snapshot = read_snapshot(path)
        except VerifyError as exc:
            entries.append(SweepEntry(entry=entry, snapshot_path=path, unreadable=exc.message))
            continue
        plan = build_plan(manifest, entry.profile, directories, stamp=False).plan
        entries.append(
            SweepEntry(
                entry=entry,
                snapshot_path=path,
                drift=reconcile(plan, snapshot, None),
            )
        )
    return Sweep(
        plant=roster.plant,
        entries=tuple(entries),
        unmatched=tuple(
            path
            for terminal, path in sorted(snapshots.items())
            if terminal not in roster
        ),
    )


def snapshot_of(path: Path) -> Snapshot:
    """Read one, with the toolkit's own error rather than a bare OSError."""
    return read_snapshot(path)


__all__ = [
    "RULE",
    "SNAPSHOT_GLOB",
    "Sweep",
    "SweepEntry",
    "find_snapshots",
    "snapshot_of",
    "sweep",
]
