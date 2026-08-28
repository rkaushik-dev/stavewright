"""The individual checks.

Each one is a class with a name and a ``check`` method, the same shape the GS1
and template rules use. That is deliberate: an operator learns one report
format and one severity scale for the whole toolkit.

The OS version checks come straight out of the AIP launch conditions. The
package refuses to install on Windows 9x, NT 4.0 and 2000, and those conditions
are written as ``VersionNT``, ``(VersionNT <> 400)`` and ``(VersionNT <> 500)``.
Rather than evaluate MSI condition syntax, the three are turned into build
numbers here and checked directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stavewright.finding import Finding, Severity
from stavewright.model.locale import code_pages_for, needs_multiple_code_pages
from stavewright.model.profile import TerminalProfile
from stavewright.plan.steps import Plan, StepKind
from stavewright.ruleset import Rule as BaseRule
from stavewright.target.base import SystemFacts, TargetSystem
from stavewright.util.msiver import InstalledProduct, UpgradeRow, evaluate_upgrades

MIN_OS_BUILD = 2600
"""Windows XP. The AIP refuses 9x, NT 4.0 and 2000."""

REFUSED_OS_BUILDS = frozenset({400, 500})
"""NT 4.0 and 2000, from the shipped launch conditions."""

DISK_HEADROOM = 1.25
"""Ask for a quarter more room than the payload needs.

The installer unpacks before it copies and Windows will not let you get to
zero without something else on the machine falling over first.
"""

BYTES_PER_STEP_ESTIMATE = 512 * 1024
"""What a step is assumed to cost when the plan carries no size information."""


@dataclass
class PreflightContext:
    """What the checks are allowed to look at."""

    plan: Plan
    profile: TerminalProfile
    facts: SystemFacts
    payload_bytes: int = 0
    installed_products: list[InstalledProduct] = field(default_factory=list)
    upgrade_rows: list[UpgradeRow] = field(default_factory=list)
    hasp_serial: str | None = None
    unfinished_journal: str | None = None
    required_fonts: frozenset[str] = frozenset()
    """Fonts the template set names.

    Empty means nobody told the checks about a template set, and the font check
    stands down rather than guessing. ``run preflight --templates`` fills it in
    from the same set reader the template commands use.
    """


class Check(BaseRule["PreflightContext", TargetSystem]):
    """A check over a planned run, told about the machine it would run on.

    The subject is the context and the context is the target, which reads
    backwards until you notice that a check is about the plan and only consults
    the machine. Renaming the two would touch every check for nothing.
    """

    name = "check"
    title = "a check"

    def _about(
        self,
        severity: Severity,
        message: str,
        subject: str | None = None,
        **extra: object,
    ) -> Finding:
        """A finding about one thing on the terminal, named third.

        Its own name rather than an override of ``_finding``, for the same
        reason as the dataset rules: the base takes the AI in that position.
        """
        return self._make(severity, message, subject=subject, context=dict(extra))


class ElevationCheck(Check):
    """Registering COM and staging drivers both need administrator."""

    name = "elevation"
    title = "running as administrator"

    NEEDS_ELEVATION = frozenset(
        {
            StepKind.REGISTER_COM,
            StepKind.STAGE_DRIVER,
            StepKind.INSTALL_SXS_ASSEMBLY,
            StepKind.INSTALL_SXS_POLICY,
            StepKind.INSTALL_FONT,
            StepKind.RUN_REDISTRIBUTABLE,
        }
    )

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if context.facts.is_elevated:
            return []
        needing = sorted(
            {step.kind.value for step in context.plan if step.kind in self.NEEDS_ELEVATION}
        )
        if not needing:
            return []
        return [
            self._about(
                Severity.ERROR,
                "this plan needs administrator and the session is not elevated",
                kinds=needing,
            )
        ]


class OsVersionCheck(Check):
    """The package refuses the operating systems the AIP refuses."""

    name = "os-version"
    title = "supported operating system"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        build = context.facts.os_build
        if build in REFUSED_OS_BUILDS:
            return [
                self._about(
                    Severity.ERROR,
                    "the package refuses this Windows version",
                    build=build,
                )
            ]
        if build < MIN_OS_BUILD:
            return [
                self._about(
                    Severity.ERROR,
                    "the package needs Windows XP or later",
                    build=build,
                    minimum=MIN_OS_BUILD,
                )
            ]
        return []


class ArchitectureCheck(Check):
    """The profile has to agree with the machine it is pointed at."""

    name = "architecture"
    title = "profile matches the machine"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if context.facts.architecture == context.profile.architecture.value:
            return []
        return [
            self._about(
                Severity.ERROR,
                "the profile and the machine disagree about the architecture",
                profile=context.profile.architecture.value,
                machine=context.facts.architecture,
            )
        ]


class FreeSpaceCheck(Check):
    """There has to be room for the payload with something left over."""

    name = "free-space"
    title = "free disk space"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        needed = context.payload_bytes or len(context.plan) * BYTES_PER_STEP_ESTIMATE
        required = int(needed * DISK_HEADROOM)
        if context.facts.free_bytes >= required:
            return []
        return [
            self._about(
                Severity.ERROR,
                "not enough free space on the system drive",
                required=required,
                available=context.facts.free_bytes,
                shortfall=required - context.facts.free_bytes,
            )
        ]


class NewerProductCheck(Check):
    """A newer build of the product blocks the install, as it does in MSI."""

    name = "newer-product"
    title = "no newer product installed"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if not context.upgrade_rows:
            return []
        decision = evaluate_upgrades(context.installed_products, context.upgrade_rows)
        findings: list[Finding] = []
        if decision.is_blocked:
            findings.append(
                self._about(
                    Severity.ERROR,
                    "a newer build of the product is already on this terminal",
                    versions=[str(product.version) for product in decision.blocking],
                )
            )
        if decision.removable:
            findings.append(
                self._about(
                    Severity.WARNING,
                    "an older build is installed and will be removed first",
                    versions=[str(product.version) for product in decision.removable],
                )
            )
        return findings


class ControllerCheck(Check):
    """A plan that programs the controller needs a controller to program."""

    name = "controller"
    title = "print head controller"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        steps = context.plan.of_kind(StepKind.PROGRAM_FPGA)
        if not steps:
            return []
        if not context.profile.machine.has_fpga:
            return [
                self._about(
                    Severity.ERROR,
                    "the plan programs a controller this machine does not have",
                    machine=context.profile.machine.value,
                )
            ]
        current = target.controller_revision()
        wanted = context.profile.fpga_revision
        if current is None:
            return [
                self._about(
                    Severity.WARNING,
                    "the controller reports no bitstream revision, it may be blank",
                    wanted=wanted,
                )
            ]
        if wanted and current != wanted:
            return [
                self._about(
                    Severity.INFO,
                    "the controller will be reprogrammed",
                    installed=current,
                    wanted=wanted,
                )
            ]
        return []


class TemplateFontCheck(Check):
    """Every font the plan does not install has to already be on the machine."""

    name = "template-font"
    title = "fonts available"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if not context.required_fonts:
            return []
        installing = {
            str(step.detail.get("font", "")).lower()
            for step in context.plan.of_kind(StepKind.INSTALL_FONT)
        }
        present = {font.lower() for font in context.facts.installed_fonts}
        missing = sorted(
            font
            for font in context.required_fonts
            if font.lower() not in installing and font.lower() not in present
        )
        if not missing:
            return []
        return [
            self._about(
                Severity.WARNING,
                "a font the templates use is neither installed nor being installed",
                fonts=missing,
            )
        ]


class CodePageCheck(Check):
    """A head renders one code page, so a mixed language set is a problem."""

    name = "code-page"
    title = "one code page per head"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        languages = context.profile.languages
        if not needs_multiple_code_pages(languages):
            return []
        return [
            self._about(
                Severity.WARNING,
                "the selected languages need more than one print head code page",
                code_pages=sorted(code_pages_for(languages)),
                languages=[entry.tag for entry in languages],
            )
        ]


class HaspCheck(Check):
    """The dongle the profile names has to be the one that is plugged in."""

    name = "hasp"
    title = "licence dongle"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        wanted = context.profile.hasp_serial
        if wanted is None:
            return []
        if context.hasp_serial is None:
            return [
                self._about(
                    Severity.ERROR,
                    "the profile names a dongle and none is attached",
                    wanted=wanted,
                )
            ]
        if context.hasp_serial != wanted:
            return [
                self._about(
                    Severity.ERROR,
                    "the attached dongle is not the one this terminal is licensed with",
                    wanted=wanted,
                    found=context.hasp_serial,
                )
            ]
        return []


class UnfinishedRunCheck(Check):
    """An unfinished journal on the bench means somebody stopped half way."""

    name = "unfinished-run"
    title = "no run left half done"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if context.unfinished_journal is None:
            return []
        return [
            self._about(
                Severity.ERROR,
                "a previous run on this terminal never finished, resume or roll it back",
                journal=context.unfinished_journal,
            )
        ]


class EmptyPlanCheck(Check):
    """A plan with nothing in it is almost always a filtering mistake."""

    name = "empty-plan"
    title = "plan has work in it"

    def check(self, context: PreflightContext, target: TargetSystem) -> list[Finding]:
        if len(context.plan):
            return []
        return [
            self._about(
                Severity.WARNING,
                "the plan has no steps, nothing applies to this terminal",
                terminal=context.profile.terminal_id,
            )
        ]


DEFAULT_CHECKS: tuple[Check, ...] = (
    UnfinishedRunCheck(),
    ElevationCheck(),
    OsVersionCheck(),
    ArchitectureCheck(),
    FreeSpaceCheck(),
    NewerProductCheck(),
    ControllerCheck(),
    CodePageCheck(),
    HaspCheck(),
    TemplateFontCheck(),
    EmptyPlanCheck(),
)


__all__ = [
    "BYTES_PER_STEP_ESTIMATE",
    "DEFAULT_CHECKS",
    "DISK_HEADROOM",
    "MIN_OS_BUILD",
    "REFUSED_OS_BUILDS",
    "ArchitectureCheck",
    "Check",
    "CodePageCheck",
    "ControllerCheck",
    "ElevationCheck",
    "EmptyPlanCheck",
    "FreeSpaceCheck",
    "HaspCheck",
    "NewerProductCheck",
    "OsVersionCheck",
    "PreflightContext",
    "TemplateFontCheck",
    "UnfinishedRunCheck",
]
