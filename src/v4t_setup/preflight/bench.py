"""Checks about the bench and the person, not about the terminal.

The checks in :mod:`v4t_setup.preflight.checks` all ask whether the machine is
ready. These ask whether the visit is: is the bundle the one that was signed
off, is the laptop's clock right, is anybody going to be able to read the
journal afterwards.

They are separate because they run at a different time. The terminal checks run
on the floor with the machine in front of you. These are worth running in the
van, and a failure here means turning round rather than standing on a line
wishing you had a memory stick.

Every one of these has cost somebody a visit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from v4t_setup.clock import now as clock_now
from v4t_setup.finding import Finding, Severity
from v4t_setup.model.manifest import Manifest
from v4t_setup.ruleset import Rule as BaseRule
from v4t_setup.ruleset import run_rules

RULE_PREFIX = "bench"

MAX_CLOCK_SKEW = timedelta(minutes=5)
"""Past this the journal timestamps stop lining up with anybody else's log.

A bench laptop that has been in a van for a month drifts, and a commissioning
record timestamped an hour before the line stopped is a record nobody trusts.
"""

MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
"""On the laptop, for the staged bundle and the journals. Not the terminal."""


@dataclass
class BenchContext:
    """What these checks are allowed to look at."""

    manifest: Manifest | None = None
    bundle_root: Path | None = None
    signature_present: bool = False
    trusted_keys: Path | None = None
    journal_root: Path | None = None
    laptop_free_bytes: int | None = None
    laptop_clock: datetime | None = None
    reference_clock: datetime | None = None
    """A time from somewhere that is not this laptop. None means nobody asked."""

    operator: str = ""
    notes: list[str] = field(default_factory=list)


class BenchCheck(BaseRule["BenchContext", None]):
    """A check over the visit rather than over the terminal."""

    name = "bench"

    def _about(
        self,
        severity: Severity,
        message: str,
        subject: str | None = None,
        **extra: object,
    ) -> Finding:
        return self._make(severity, message, subject=subject, context=dict(extra))


class BundleSignedCheck(BenchCheck):
    """A bundle that goes onto a customer's line should be signed."""

    name = f"{RULE_PREFIX}/signature"
    title = "the bundle is signed"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        if context.manifest is None:
            return []
        if context.signature_present:
            return []
        return [
            self._about(
                Severity.WARNING,
                "this bundle has no signature beside it",
                "signature",
                bundle=str(context.bundle_root or ""),
            )
        ]


class TrustedKeysCheck(BenchCheck):
    """A signature is worth nothing without the keys to check it against."""

    name = f"{RULE_PREFIX}/keys"
    title = "trusted keys are on the laptop"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        if not context.signature_present:
            return []
        keys = context.trusted_keys
        if keys is not None and keys.is_dir() and any(keys.glob("*.pem")):
            return []
        return [
            self._about(
                Severity.ERROR,
                "the bundle is signed and this laptop has no keys to check it with",
                "trusted_keys",
                looked_in=str(keys or ""),
            )
        ]


class ClockCheck(BenchCheck):
    """The laptop's clock has to agree with something else."""

    name = f"{RULE_PREFIX}/clock"
    title = "the laptop clock is right"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        reference = context.reference_clock
        if reference is None:
            return [
                self._about(
                    Severity.INFO,
                    "nothing checked this laptop's clock against anything",
                    "clock",
                )
            ]
        laptop = context.laptop_clock or clock_now()
        skew = abs(laptop - reference)
        if skew <= MAX_CLOCK_SKEW:
            return []
        return [
            self._about(
                Severity.ERROR,
                "the laptop clock is far enough out that the journal will not line up",
                "clock",
                skew_seconds=int(skew.total_seconds()),
                limit_seconds=int(MAX_CLOCK_SKEW.total_seconds()),
            )
        ]


class LaptopSpaceCheck(BenchCheck):
    """Room on the laptop for the bundle and the journals it will write."""

    name = f"{RULE_PREFIX}/space"
    title = "room on the laptop"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        free = context.laptop_free_bytes
        if free is None or free >= MIN_FREE_BYTES:
            return []
        return [
            self._about(
                Severity.ERROR,
                "this laptop is nearly full, and a failed write mid run is the worst case",
                "free_space",
                free_bytes=free,
                wanted_bytes=MIN_FREE_BYTES,
            )
        ]


class JournalWritableCheck(BenchCheck):
    """Somewhere to write the record of what was done."""

    name = f"{RULE_PREFIX}/journal"
    title = "the journal directory is writable"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        root = context.journal_root
        if root is None:
            return [
                self._about(
                    Severity.ERROR,
                    "no journal directory is configured, so nothing would be recorded",
                    "journal_root",
                )
            ]
        if root.is_dir():
            return []
        if root.exists():
            return [
                self._about(
                    Severity.ERROR,
                    "the journal path is a file rather than a directory",
                    "journal_root",
                    path=str(root),
                )
            ]
        # Not an error. The executor creates it, and a bench that has never
        # run anything has no journal directory yet.
        return [
            self._about(
                Severity.INFO,
                "the journal directory does not exist yet and will be created",
                "journal_root",
                path=str(root),
            )
        ]


class OperatorCheck(BenchCheck):
    """Somebody's name on the record."""

    name = f"{RULE_PREFIX}/operator"
    title = "the operator is named"

    def check(self, context: BenchContext, _: None) -> list[Finding]:
        if context.operator.strip():
            return []
        return [
            self._about(
                Severity.WARNING,
                "nobody is named on this run, and the customer will ask who did it",
                "operator",
            )
        ]


DEFAULT_BENCH_CHECKS: tuple[BenchCheck, ...] = (
    BundleSignedCheck(),
    TrustedKeysCheck(),
    ClockCheck(),
    LaptopSpaceCheck(),
    JournalWritableCheck(),
    OperatorCheck(),
)


def check_bench(
    context: BenchContext,
    checks: tuple[BenchCheck, ...] = DEFAULT_BENCH_CHECKS,
    *,
    waive: dict[str, str] | None = None,
) -> list[Finding]:
    """Run the bench checks. Nothing here touches a terminal."""
    return run_rules(checks, context, None, skip=set(waive or {}))


__all__ = [
    "DEFAULT_BENCH_CHECKS",
    "MAX_CLOCK_SKEW",
    "MIN_FREE_BYTES",
    "RULE_PREFIX",
    "BenchCheck",
    "BenchContext",
    "BundleSignedCheck",
    "ClockCheck",
    "JournalWritableCheck",
    "LaptopSpaceCheck",
    "OperatorCheck",
    "TrustedKeysCheck",
    "check_bench",
]
