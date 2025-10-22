"""What has been done to a terminal, read back out of the journals.

Six weeks after a line stops, the question is never "what is on this machine",
which reconcile answers. It is "what happened to it, and when, and did anybody
finish". That is a question about the journals, and there is one per run.

A journal directory is the record. This reads all of them, sorts by when the
run started, and works out which terminal each belongs to and how it ended.

A journal that will not verify is kept and marked rather than dropped. A run
whose record has been damaged is exactly the run somebody needs to know about,
and silently skipping it produces a history that looks tidy and is missing the
interesting part.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from v4t_setup.apply.journal import (
    JournalState,
    Record,
    RecordKind,
    read_records,
    replay,
    verify_chain,
)
from v4t_setup.errors import JournalError, VerifyError

JOURNAL_GLOB = "run-*.jsonl"


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One commissioning run, as its journal describes it."""

    path: Path
    run_id: str = ""
    terminal: str = ""
    plan_digest: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outcome: str = ""
    completed: int = 0
    skipped: int = 0
    failed_step: str | None = None
    rolled_back: int = 0
    damaged: str = ""
    """Why the journal did not verify, empty when it did."""

    @property
    def is_readable(self) -> bool:
        return not self.damaged

    @property
    def is_complete(self) -> bool:
        return self.is_readable and self.outcome == "complete"

    @property
    def is_unfinished(self) -> bool:
        """True when the run never wrote an end record.

        The interesting state. Somebody stopped half way and the terminal is in
        whatever state that left it.
        """
        return self.is_readable and not self.outcome

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def describe(self) -> str:
        when = self.started_at.isoformat(sep=" ") if self.started_at else "unknown"
        if self.damaged:
            return f"{self.path.name}: unreadable, {self.damaged}"
        verdict = self.outcome or "never finished"
        return f"{when}  {self.terminal or '?'}  {self.run_id or '?'}  {verdict}"


def _timestamps(records: list[Record]) -> tuple[datetime | None, datetime | None]:
    if not records:
        return None, None

    def at(record: Record) -> datetime | None:
        try:
            return datetime.fromisoformat(record.at)
        except ValueError:
            return None

    started = at(records[0])
    ended = next(
        (at(record) for record in reversed(records) if record.kind is RecordKind.END), None
    )
    return started, ended


def summarise_journal(path: Path) -> RunSummary:
    """Read one journal into a summary.

    A journal that fails verification still produces a summary, with what could
    be read and the reason it did not verify.

    :raises VerifyError: only when the file cannot be read at all.
    """
    try:
        records = read_records(path)
    except JournalError as exc:
        raise VerifyError(
            "cannot read journal",
            context={"path": str(path), "detail": exc.message},
        ) from exc

    damaged = ""
    try:
        verify_chain(records)
    except JournalError as exc:
        damaged = exc.message

    state: JournalState = replay(records)
    started, ended = _timestamps(records)
    return RunSummary(
        path=path,
        run_id=state.run_id,
        terminal=state.terminal,
        plan_digest=state.plan_digest,
        started_at=started,
        finished_at=ended,
        outcome=state.outcome,
        completed=len(state.completed),
        skipped=len(state.skipped),
        failed_step=state.failed,
        rolled_back=len(state.rolled_back),
        damaged=damaged,
    )


@dataclass
class History:
    """Every run a journal directory holds."""

    runs: list[RunSummary] = field(default_factory=list)
    root: Path | None = None

    def __len__(self) -> int:
        return len(self.runs)

    def __iter__(self) -> Iterator[RunSummary]:
        return iter(self.runs)

    def for_terminal(self, terminal: str) -> list[RunSummary]:
        """Runs against one terminal, oldest first."""
        return [run for run in self.runs if run.terminal == terminal]

    @property
    def terminals(self) -> list[str]:
        """Every terminal that appears, sorted."""
        return sorted({run.terminal for run in self.runs if run.terminal})

    @property
    def unfinished(self) -> list[RunSummary]:
        """Runs that never wrote an end record."""
        return [run for run in self.runs if run.is_unfinished]

    @property
    def damaged(self) -> list[RunSummary]:
        """Journals that did not verify."""
        return [run for run in self.runs if not run.is_readable]

    def latest(self, terminal: str) -> RunSummary | None:
        """The most recent run against *terminal*, or None."""
        runs = self.for_terminal(terminal)
        return runs[-1] if runs else None

    def counts(self) -> dict[str, int]:
        """How many runs ended each way."""
        counts: dict[str, int] = {}
        for run in self.runs:
            key = "unreadable" if not run.is_readable else (run.outcome or "unfinished")
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def lines(self) -> list[str]:
        where = str(self.root) if self.root else "(in memory)"
        head = f"{where}: {len(self.runs)} runs across {len(self.terminals)} terminals"
        return [head, *(run.describe() for run in self.runs)]


def read_history(root: Path, *, pattern: str = JOURNAL_GLOB) -> History:
    """Read every journal under *root*.

    Sorted by when the run started, with journals that carry no readable start
    time last so they do not silently sort to the beginning of time.

    :raises VerifyError: when *root* is not a directory.
    """
    if not root.is_dir():
        raise VerifyError("journal directory does not exist", context={"path": str(root)})
    runs = [summarise_journal(path) for path in sorted(root.glob(pattern))]
    runs.sort(key=lambda run: (run.started_at is None, run.started_at or datetime.min))
    return History(runs=runs, root=root)


def find_unfinished(root: Path, terminal: str) -> RunSummary | None:
    """The most recent unfinished run against *terminal*, or None.

    Preflight calls this. A terminal with a half finished run on it should not
    be started again until somebody has resumed or rolled back the first one.
    """
    if not root.is_dir():
        return None
    for run in reversed(read_history(root).for_terminal(terminal)):
        if run.is_unfinished:
            return run
    return None


__all__ = [
    "JOURNAL_GLOB",
    "History",
    "RunSummary",
    "find_unfinished",
    "read_history",
    "summarise_journal",
]
