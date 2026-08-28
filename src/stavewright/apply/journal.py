"""An append only, hash chained record of what a run did.

One JSON object per line. Each record carries the hash of the one before it, so
a journal that has been truncated, reordered or edited fails verification
instead of quietly replaying wrong.

That matters more than it sounds. A rollback is driven off the journal, and a
rollback driven off a journal that has lost its last two records will leave two
files on the terminal that nobody knows are there. Better to refuse and make
somebody look.

The journal is written with a flush per record. It is small, the runs are not
frequent, and a journal that is missing its last record because it was still in
a buffer when the power went is exactly the case it exists for.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

from stavewright import clock
from stavewright.__about__ import JOURNAL_SCHEMA_VERSION
from stavewright.errors import JournalError
from stavewright.util.fsops import ensure_dir

GENESIS = "0" * 64
"""The previous hash of the first record."""


class RecordKind(StrEnum):
    """What a journal line says happened."""

    BEGIN = "begin"
    STEP_START = "step-start"
    STEP_OK = "step-ok"
    STEP_SKIPPED = "step-skipped"
    STEP_FAILED = "step-failed"
    ROLLBACK_START = "rollback-start"
    ROLLBACK_OK = "rollback-ok"
    ROLLBACK_FAILED = "rollback-failed"
    END = "end"


TERMINAL_KINDS = frozenset({RecordKind.END})
COMPLETED_KINDS = frozenset({RecordKind.STEP_OK, RecordKind.STEP_SKIPPED})


@dataclass(frozen=True, slots=True)
class Record:
    """One line."""

    sequence: int
    kind: RecordKind
    at: str
    previous: str
    digest: str
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def step_id(self) -> str | None:
        value = self.fields.get("step_id")
        return str(value) if value is not None else None

    def body(self) -> dict[str, Any]:
        """The part the hash is taken over."""
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "at": self.at,
            "previous": self.previous,
            "fields": self.fields,
        }

    def to_line(self) -> str:
        payload = {**self.body(), "digest": self.digest}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def describe(self) -> str:
        step = f" {self.step_id}" if self.step_id else ""
        return f"{self.sequence:4d} {self.kind.value}{step}"


def digest_for(body: dict[str, Any]) -> str:
    """Hash a record body, chaining it to its predecessor."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Journal:
    """Writes records to a file, maintaining the chain."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None
        self._sequence = 0
        self._previous = GENESIS

    def __enter__(self) -> Journal:
        ensure_dir(self.path.parent)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Flush and close. Safe to call more than once."""
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None

    @property
    def sequence(self) -> int:
        """How many records have been written through this object."""
        return self._sequence

    @property
    def head(self) -> str:
        """The hash of the last record written."""
        return self._previous

    def append(self, kind: RecordKind, **fields: Any) -> Record:
        """Write one record.

        :raises JournalError: when the journal is not open.
        """
        if self._handle is None:
            raise JournalError("journal is not open", context={"path": str(self.path)})
        body = {
            "sequence": self._sequence,
            "kind": kind.value,
            "at": clock.stamp(),
            "previous": self._previous,
            "fields": fields,
        }
        record = Record(
            sequence=self._sequence,
            kind=kind,
            at=body["at"],  # type: ignore[arg-type]
            previous=self._previous,
            digest=digest_for(body),
            fields=dict(fields),
        )
        self._handle.write(record.to_line() + "\n")
        self._handle.flush()
        self._sequence += 1
        self._previous = record.digest
        return record

    def begin(self, plan_digest: str, terminal: str, run_id: str) -> Record:
        """Open a run."""
        return self.append(
            RecordKind.BEGIN,
            schema_version=JOURNAL_SCHEMA_VERSION,
            plan=plan_digest,
            terminal=terminal,
            run_id=run_id,
        )

    def end(self, outcome: str) -> Record:
        """Close a run."""
        return self.append(RecordKind.END, outcome=outcome)


def parse_line(text: str, line_number: int) -> Record:
    """Parse one journal line.

    :raises JournalError: when it is not a well formed record.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JournalError(
            "journal line is not valid JSON",
            context={"line": line_number, "detail": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise JournalError("journal line is not an object", context={"line": line_number})
    missing = [
        name
        for name in ("sequence", "kind", "at", "previous", "digest")
        if name not in payload
    ]
    if missing:
        raise JournalError(
            "journal line is missing fields",
            context={"line": line_number, "missing": missing},
        )
    try:
        kind = RecordKind(payload["kind"])
    except ValueError:
        raise JournalError(
            "journal line has an unknown record kind",
            context={"line": line_number, "kind": payload["kind"]},
        ) from None
    return Record(
        sequence=int(payload["sequence"]),
        kind=kind,
        at=str(payload["at"]),
        previous=str(payload["previous"]),
        digest=str(payload["digest"]),
        fields=dict(payload.get("fields") or {}),
    )


def read_records(path: Path) -> list[Record]:
    """Read every record, without checking the chain.

    :raises JournalError: when the file cannot be read or a line will not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JournalError(
            "cannot read journal",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    records: list[Record] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            records.append(parse_line(line, number))
    return records


def verify_chain(records: list[Record]) -> None:
    """Check the sequence numbers and the hash chain.

    :raises JournalError: on a gap, a wrong predecessor hash, or a record whose
        own hash does not match its content.
    """
    previous = GENESIS
    for position, record in enumerate(records):
        if record.sequence != position:
            raise JournalError(
                "journal sequence has a gap",
                context={"expected": position, "found": record.sequence},
            )
        if record.previous != previous:
            raise JournalError(
                "journal record does not chain to the one before it",
                context={"sequence": record.sequence, "expected": previous},
            )
        recomputed = digest_for(record.body())
        if recomputed != record.digest:
            raise JournalError(
                "journal record has been altered",
                context={"sequence": record.sequence},
            )
        previous = record.digest


def read_journal(path: Path, *, verify: bool = True) -> list[Record]:
    """Read a journal and, by default, check its chain."""
    records = read_records(path)
    if verify:
        verify_chain(records)
    return records


@dataclass
class JournalState:
    """What a journal says about a run."""

    run_id: str = ""
    terminal: str = ""
    plan_digest: str = ""
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: str | None = None
    failure_detail: str = ""
    rolled_back: list[str] = field(default_factory=list)
    finished: bool = False
    outcome: str = ""

    @property
    def is_complete(self) -> bool:
        """True when the run reached its end record without failing."""
        return self.finished and self.failed is None

    @property
    def needs_rollback(self) -> list[str]:
        """Completed steps that have not been rolled back, newest first.

        Reversed because a rollback undoes in the opposite order to the apply.
        A directory created before a file has to be removed after it.
        """
        done = set(self.rolled_back)
        return [step for step in reversed(self.completed) if step not in done]


def replay(records: list[Record]) -> JournalState:
    """Fold a journal into the state it describes."""
    state = JournalState()
    for record in records:
        match record.kind:
            case RecordKind.BEGIN:
                state.run_id = str(record.fields.get("run_id", ""))
                state.terminal = str(record.fields.get("terminal", ""))
                state.plan_digest = str(record.fields.get("plan", ""))
            case RecordKind.STEP_OK:
                if record.step_id:
                    state.completed.append(record.step_id)
            case RecordKind.STEP_SKIPPED:
                if record.step_id:
                    state.skipped.append(record.step_id)
            case RecordKind.STEP_FAILED:
                state.failed = record.step_id
                state.failure_detail = str(record.fields.get("detail", ""))
            case RecordKind.ROLLBACK_OK:
                if record.step_id:
                    state.rolled_back.append(record.step_id)
            case RecordKind.END:
                state.finished = True
                state.outcome = str(record.fields.get("outcome", ""))
            case _:
                pass
    return state


def iter_steps(records: list[Record]) -> Iterator[tuple[str, RecordKind]]:
    """Yield each step id with the outcome kind that closed it."""
    closing = COMPLETED_KINDS | {RecordKind.STEP_FAILED}
    for record in records:
        if record.kind in closing and record.step_id:
            yield record.step_id, record.kind


__all__ = [
    "COMPLETED_KINDS",
    "GENESIS",
    "TERMINAL_KINDS",
    "Journal",
    "JournalState",
    "Record",
    "RecordKind",
    "digest_for",
    "iter_steps",
    "parse_line",
    "read_journal",
    "read_records",
    "replay",
    "verify_chain",
]
