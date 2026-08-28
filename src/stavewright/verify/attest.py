"""The one page a regulated customer asks for.

Pharmaceutical and food customers audit their coding equipment, and the
question they ask is always the same: what was installed on this terminal, when,
by which version of which bundle, and who says so. The journal answers it and
nobody outside commissioning can read a journal.

An attestation is that answer in one document, small enough to print and hash
to something a signature can cover. It carries what was applied rather than
what was planned, because a plan is a intention and an audit is about facts.

Three properties it has to have, and each one cost something to get right:

It is derived, never typed. Every field comes out of the plan, the apply result
or the drift report. Anything a person could fill in is a field a person will
fill in wrong six months later.

Its canonical form is stable. The digest has to come out the same on a bench
laptop in 2026 and on a build server in 2031, so the fields are sorted and the
timestamps are ISO 8601 in UTC.

It says when it does not know. A terminal that was never reconciled after the
run gets ``verified: null`` rather than false, because "nobody checked" and
"checked and it had drifted" are opposite answers and both fail an audit
differently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from stavewright import clock
from stavewright.__about__ import __version__
from stavewright.apply.executor import ApplyResult
from stavewright.apply.executor import Outcome as ApplyOutcome
from stavewright.errors import VerifyError
from stavewright.finding import Finding, Severity
from stavewright.plan.steps import Plan
from stavewright.util.digest import Digest, digest_bytes
from stavewright.util.fsops import atomic_write_text
from stavewright.verify.drift import DriftReport

SCHEMA = "stavewright/attestation/1"
RULE = "attestation"


@dataclass(frozen=True, slots=True)
class Attestation:
    """What was done to one terminal, in a form somebody can audit."""

    terminal: str
    run_id: str
    plan_digest: str
    outcome: str
    steps_applied: int = 0
    steps_skipped: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    verified: bool | None = None
    """True when a reconcile ran and found nothing. None when none ran."""

    drift_records: int = 0
    toolkit_version: str = __version__
    notes: str = ""
    changed_steps: tuple[str, ...] = field(default=())

    @property
    def is_complete(self) -> bool:
        return self.outcome == ApplyOutcome.COMPLETE.value

    @property
    def is_auditable(self) -> bool:
        """Whether this stands on its own as evidence.

        A run that did not finish is not evidence of anything, and a run
        nobody reconciled afterwards is evidence of what the toolkit did
        rather than of what is on the machine.
        """
        return self.is_complete and self.verified is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "terminal": self.terminal,
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "outcome": self.outcome,
            "steps_applied": self.steps_applied,
            "steps_skipped": self.steps_skipped,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "verified": self.verified,
            "drift_records": self.drift_records,
            "toolkit_version": self.toolkit_version,
            "notes": self.notes,
            "changed_steps": list(self.changed_steps),
        }

    def canonical(self) -> str:
        """The bytes a signature covers.

        Sorted keys, no whitespace and no timestamp of its own, so that the
        same run attested twice produces the same digest. An attestation that
        digests differently every time it is written cannot be compared to the
        copy in the audit file.
        """
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> Digest:
        return digest_bytes(self.canonical().encode("utf-8"))

    def dumps(self, *, indent: int = 2) -> str:
        payload = {**self.to_dict(), "digest": str(self.digest())}
        return json.dumps(payload, sort_keys=True, indent=indent) + "\n"

    def write(self, path: Path) -> None:
        atomic_write_text(path, self.dumps())

    def describe(self) -> str:
        state = "verified" if self.verified else "unverified"
        if self.verified is None:
            state = "never reconciled"
        when = self.finished_at.date().isoformat() if self.finished_at else "unknown date"
        return f"{self.terminal} {self.outcome}, {state}, {when}, run {self.run_id}"


def attest(
    plan: Plan,
    result: ApplyResult,
    drift: DriftReport | None = None,
    *,
    notes: str = "",
) -> Attestation:
    """Build an attestation from what a run produced.

    :param drift: the reconcile that followed, when one did. Leaving it out is
        recorded as "never reconciled" rather than as a pass.
    """
    return Attestation(
        terminal=plan.profile.terminal_id,
        run_id=result.run_id,
        plan_digest=str(plan.digest()),
        outcome=result.outcome.value,
        steps_applied=sum(1 for step in result.applied if step.changed),
        steps_skipped=sum(1 for step in result.applied if step.skipped),
        started_at=plan.created_at,
        finished_at=clock.now(),
        verified=None if drift is None else drift.is_clean,
        drift_records=0 if drift is None else len(drift),
        notes=notes,
        changed_steps=tuple(
            step.step_id for step in result.applied if step.changed
        ),
    )


def loads(text: str) -> Attestation:
    """Read an attestation back.

    :raises VerifyError: when the text is not JSON, is not an attestation, or
        carries a digest that does not match its own contents.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerifyError("attestation is not valid JSON", context={"detail": str(exc)}) from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise VerifyError(
            "this file is not an attestation this toolkit wrote",
            context={"schema": str(payload.get("schema")) if isinstance(payload, dict) else ""},
        )
    stated = payload.pop("digest", None)
    built = Attestation(
        terminal=str(payload.get("terminal", "")),
        run_id=str(payload.get("run_id", "")),
        plan_digest=str(payload.get("plan_digest", "")),
        outcome=str(payload.get("outcome", "")),
        steps_applied=int(payload.get("steps_applied", 0)),
        steps_skipped=int(payload.get("steps_skipped", 0)),
        started_at=_moment(payload.get("started_at")),
        finished_at=_moment(payload.get("finished_at")),
        verified=payload.get("verified"),
        drift_records=int(payload.get("drift_records", 0)),
        toolkit_version=str(payload.get("toolkit_version", "")),
        notes=str(payload.get("notes", "")),
        changed_steps=tuple(str(name) for name in payload.get("changed_steps", [])),
    )
    if stated is not None and stated != str(built.digest()):
        raise VerifyError(
            "the attestation does not match its own digest",
            context={"stated": str(stated), "found": str(built.digest())},
        )
    return built


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise VerifyError(
            "an attestation timestamp is not a date and time",
            context={"value": value, "detail": str(exc)},
        ) from exc


def read_attestation(path: Path) -> Attestation:
    """Read one from disk.

    :raises VerifyError: when the file cannot be read or does not hold one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerifyError(
            "cannot read attestation",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return loads(text)


def check_attestation(attestation: Attestation, plan: Plan | None = None) -> list[Finding]:
    """Say what is wrong with an attestation as evidence.

    :param plan: the plan it should describe, when the auditor has it. Without
        one the plan digest cannot be checked and is not reported on.
    """
    findings: list[Finding] = []

    def add(severity: Severity, message: str, subject: str, **context: object) -> None:
        findings.append(
            Finding(
                rule=RULE,
                severity=severity,
                message=message,
                subject=subject,
                context=dict(context),
            )
        )

    if not attestation.is_complete:
        add(
            Severity.ERROR,
            "this run did not finish, so it is not evidence of anything",
            "outcome",
            outcome=attestation.outcome,
        )
    if attestation.verified is None:
        add(
            Severity.WARNING,
            "nobody reconciled this terminal after the run",
            "verified",
        )
    elif not attestation.verified:
        add(
            Severity.ERROR,
            "the terminal had drifted from the bundle when it was last checked",
            "verified",
            records=attestation.drift_records,
        )
    if not attestation.run_id:
        add(Severity.ERROR, "the attestation names no run", "run_id")
    if attestation.finished_at is None:
        add(Severity.WARNING, "the attestation carries no finish time", "finished_at")
    if plan is not None and attestation.plan_digest != str(plan.digest()):
        add(
            Severity.ERROR,
            "this attestation is for a different plan",
            "plan_digest",
            attested=attestation.plan_digest,
            plan=str(plan.digest()),
        )
    return findings


__all__ = [
    "RULE",
    "SCHEMA",
    "Attestation",
    "attest",
    "check_attestation",
    "loads",
    "read_attestation",
]
