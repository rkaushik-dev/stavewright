from __future__ import annotations

import pytest

from v4t_setup.__about__ import UPGRADE_CODE
from v4t_setup.finding import Severity
from v4t_setup.plan.steps import Plan, Step, StepKind
from v4t_setup.preflight.checks import (
    DEFAULT_CHECKS,
    MIN_OS_BUILD,
    ArchitectureCheck,
    Check,
    CodePageCheck,
    ControllerCheck,
    ElevationCheck,
    EmptyPlanCheck,
    FreeSpaceCheck,
    HaspCheck,
    NewerProductCheck,
    OsVersionCheck,
    PreflightContext,
    TemplateFontCheck,
    UnfinishedRunCheck,
)
from v4t_setup.target.memory import MemoryTarget
from v4t_setup.util.msiver import (
    InstalledProduct,
    MsiVersion,
    UpgradeAttributes,
    UpgradeRow,
)


@pytest.fixture
def target():
    return MemoryTarget()


def plan_with(profile, *kinds: StepKind) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            Step(step_id=f"s{n}", kind=kind, title=f"step {n}")
            for n, kind in enumerate(kinds)
        ],
    )


def context_for(profile, target, plan=None, **kwargs) -> PreflightContext:
    return PreflightContext(
        plan=plan if plan is not None else Plan(profile=profile, steps=[]),
        profile=profile,
        facts=target.facts(),
        **kwargs,
    )


def test_every_default_check_has_a_distinct_name():
    names = [check.name for check in DEFAULT_CHECKS]
    assert len(set(names)) == len(names)


def test_every_default_check_has_a_title():
    assert all(check.title for check in DEFAULT_CHECKS)


def test_the_base_check_is_abstract(profile, target):
    with pytest.raises(NotImplementedError):
        Check().check(context_for(profile, target), target)


def test_elevation_is_not_needed_for_a_plan_that_only_copies(profile, target):
    target.is_elevated = False
    plan = plan_with(profile, StepKind.COPY_FILE, StepKind.CREATE_DIRECTORY)
    assert ElevationCheck().check(context_for(profile, target, plan), target) == []


def test_elevation_is_needed_to_register_com(profile, target):
    target.is_elevated = False
    plan = plan_with(profile, StepKind.COPY_FILE, StepKind.REGISTER_COM)
    findings = ElevationCheck().check(context_for(profile, target, plan), target)
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["kinds"] == ["register-com"]


def test_an_elevated_session_passes(profile, target):
    plan = plan_with(profile, StepKind.REGISTER_COM, StepKind.STAGE_DRIVER)
    assert ElevationCheck().check(context_for(profile, target, plan), target) == []


@pytest.mark.parametrize("build", [400, 500])
def test_the_refused_windows_versions(profile, target, build):
    target.os_build = build
    findings = OsVersionCheck().check(context_for(profile, target), target)
    assert findings[0].severity is Severity.ERROR
    assert "refuses this Windows version" in findings[0].message


def test_anything_older_than_xp_is_refused(profile, target):
    target.os_build = 1998
    findings = OsVersionCheck().check(context_for(profile, target), target)
    assert findings[0].context["minimum"] == MIN_OS_BUILD


@pytest.mark.parametrize("build", [2600, 7601, 9600, 19045])
def test_supported_windows_versions_pass(profile, target, build):
    target.os_build = build
    assert OsVersionCheck().check(context_for(profile, target), target) == []


def test_a_matching_architecture_passes(profile, target):
    assert ArchitectureCheck().check(context_for(profile, target), target) == []


def test_an_x86_profile_on_an_x64_machine_is_an_error(profile_x86, target):
    findings = ArchitectureCheck().check(context_for(profile_x86, target), target)
    assert len(findings) == 1
    assert findings[0].context["profile"] == "x86"
    assert findings[0].context["machine"] == "x64"


def test_enough_free_space_passes(profile, target):
    context = context_for(profile, target, payload_bytes=1024)
    assert FreeSpaceCheck().check(context, target) == []


def test_not_enough_free_space_is_an_error(profile, target):
    target.free_bytes = 1000
    context = context_for(profile, target, payload_bytes=10_000)
    findings = FreeSpaceCheck().check(context, target)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["shortfall"] == 12_500 - 1000


def test_free_space_asks_for_headroom(profile, target):
    target.free_bytes = 10_000
    context = context_for(profile, target, payload_bytes=9_000)
    assert FreeSpaceCheck().check(context, target)


def test_free_space_estimates_from_the_step_count_when_it_has_to(profile, target):
    target.free_bytes = 1
    plan = plan_with(profile, StepKind.COPY_FILE, StepKind.COPY_FILE)
    findings = FreeSpaceCheck().check(context_for(profile, target, plan), target)
    assert findings[0].context["required"] > 0


def _upgrade_rows():
    shipped = MsiVersion.parse("1.0.0")
    return [
        UpgradeRow(
            upgrade_code=UPGRADE_CODE,
            action_property="OLDPRODUCTS",
            version_max=shipped,
            attributes=UpgradeAttributes.VERSION_MIN_INCLUSIVE,
        ),
        UpgradeRow(
            upgrade_code=UPGRADE_CODE,
            action_property="AI_NEWERPRODUCTFOUND",
            version_min=shipped,
            attributes=UpgradeAttributes.ONLY_DETECT,
        ),
    ]


def _installed(version: str):
    return InstalledProduct(
        product_code="{BA4D6ED3-C916-4F0E-BB5B-A197DA437380}",
        upgrade_code=UPGRADE_CODE,
        version=MsiVersion.parse(version),
    )


def test_a_clean_terminal_passes_the_upgrade_check(profile, target):
    context = context_for(profile, target, upgrade_rows=_upgrade_rows())
    assert NewerProductCheck().check(context, target) == []


def test_a_newer_product_blocks(profile, target):
    context = context_for(
        profile, target, upgrade_rows=_upgrade_rows(), installed_products=[_installed("1.2.0")]
    )
    findings = NewerProductCheck().check(context, target)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["versions"] == ["1.2.0"]


def test_an_older_product_is_only_a_warning(profile, target):
    context = context_for(
        profile, target, upgrade_rows=_upgrade_rows(), installed_products=[_installed("0.9.0")]
    )
    findings = NewerProductCheck().check(context, target)
    assert findings[0].severity is Severity.WARNING
    assert "will be removed first" in findings[0].message


def test_no_upgrade_rows_means_no_check(profile, target):
    context = context_for(profile, target, installed_products=[_installed("1.2.0")])
    assert NewerProductCheck().check(context, target) == []


def test_no_bitstream_step_means_no_controller_check(profile, target):
    assert ControllerCheck().check(context_for(profile, target), target) == []


def test_a_mini_cannot_be_given_a_bitstream(profile_mini, target):
    plan = plan_with(profile_mini, StepKind.PROGRAM_FPGA)
    findings = ControllerCheck().check(context_for(profile_mini, target, plan), target)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["machine"] == "s-jet-mini"


def test_a_blank_controller_is_a_warning(profile, target):
    plan = plan_with(profile, StepKind.PROGRAM_FPGA)
    findings = ControllerCheck().check(context_for(profile, target, plan), target)
    assert findings[0].severity is Severity.WARNING
    assert "may be blank" in findings[0].message


def test_reprogramming_a_controller_is_noted(profile, target):
    target.installed_revision = "PCDC-1.3"
    plan = plan_with(profile, StepKind.PROGRAM_FPGA)
    findings = ControllerCheck().check(context_for(profile, target, plan), target)
    assert findings[0].severity is Severity.INFO
    assert findings[0].context["installed"] == "PCDC-1.3"


def test_a_controller_already_on_the_right_revision_is_quiet(profile, target):
    target.installed_revision = "PCDC-1.4"
    plan = plan_with(profile, StepKind.PROGRAM_FPGA)
    assert ControllerCheck().check(context_for(profile, target, plan), target) == []


def test_one_code_page_is_fine(profile, target):
    western = profile.with_languages(["en-US", "de-DE", "fr-FR"])
    assert CodePageCheck().check(context_for(western, target), target) == []


def test_mixed_code_pages_are_a_warning(profile, target):
    mixed = profile.with_languages(["ru-RU", "tr-TR"])
    findings = CodePageCheck().check(context_for(mixed, target), target)
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["code_pages"] == [1251, 1254]


def test_no_languages_is_fine(profile, target):
    assert CodePageCheck().check(context_for(profile, target), target) == []


def test_no_dongle_expected_means_no_check(profile, target):
    assert HaspCheck().check(context_for(profile, target), target) == []


def test_a_missing_dongle_is_an_error(profile, target):
    licensed = profile.model_copy(update={"hasp_serial": "HASP-0042"})
    findings = HaspCheck().check(context_for(licensed, target), target)
    assert findings[0].severity is Severity.ERROR
    assert "none is attached" in findings[0].message


def test_the_wrong_dongle_is_an_error(profile, target):
    licensed = profile.model_copy(update={"hasp_serial": "HASP-0042"})
    context = context_for(licensed, target, hasp_serial="HASP-0099")
    findings = HaspCheck().check(context, target)
    assert findings[0].context["found"] == "HASP-0099"


def test_the_right_dongle_passes(profile, target):
    licensed = profile.model_copy(update={"hasp_serial": "HASP-0042"})
    context = context_for(licensed, target, hasp_serial="HASP-0042")
    assert HaspCheck().check(context, target) == []


def test_a_font_already_on_the_machine_passes(profile, target):
    target.fonts.add("OCR-B")
    context = context_for(profile, target, required_fonts=frozenset({"OCR-B"}))
    assert TemplateFontCheck().check(context, target) == []


def test_a_font_the_plan_installs_passes(profile, target):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="font",
                kind=StepKind.INSTALL_FONT,
                title="install OCR-B",
                detail={"font": "OCR-B"},
            )
        ],
    )
    context = context_for(profile, target, plan, required_fonts=frozenset({"OCR-B"}))
    assert TemplateFontCheck().check(context, target) == []


def test_a_font_that_is_neither_is_a_warning(profile, target):
    context = context_for(profile, target, required_fonts=frozenset({"OCR-B"}))
    findings = TemplateFontCheck().check(context, target)
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["fonts"] == ["OCR-B"]


def test_no_required_fonts_means_no_check(profile, target):
    assert TemplateFontCheck().check(context_for(profile, target), target) == []


def test_no_unfinished_run_passes(profile, target):
    assert UnfinishedRunCheck().check(context_for(profile, target), target) == []


def test_an_unfinished_run_is_an_error(profile, target):
    context = context_for(profile, target, unfinished_journal="_journal/run-0041.jsonl")
    findings = UnfinishedRunCheck().check(context, target)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["journal"] == "_journal/run-0041.jsonl"


def test_an_empty_plan_is_a_warning(profile, target):
    findings = EmptyPlanCheck().check(context_for(profile, target), target)
    assert findings[0].severity is Severity.WARNING


def test_a_plan_with_work_in_it_is_fine(profile, target):
    plan = plan_with(profile, StepKind.COPY_FILE)
    assert EmptyPlanCheck().check(context_for(profile, target, plan), target) == []
