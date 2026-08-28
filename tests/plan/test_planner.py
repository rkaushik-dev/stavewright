from __future__ import annotations

import pytest

from stavewright.errors import DirectoryResolutionError, ManifestError, PlanCycleError
from stavewright.model.manifest import Manifest
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.plan.planner import (
    KIND_PRIORITY,
    STEP_KIND_FOR,
    build_graph,
    build_plan,
    explain_order,
)
from stavewright.plan.rules import OrderingRule
from stavewright.plan.steps import StepKind


def plan_for(manifest, profile, directory_table, **kwargs):
    return build_plan(manifest, profile, directory_table, stamp=False, **kwargs)


def test_every_payload_kind_maps_to_a_step_kind():
    assert set(STEP_KIND_FOR) == set(PayloadKind)


def test_every_payload_kind_has_a_tie_break_weight():
    assert set(KIND_PRIORITY) == set(PayloadKind)


def test_tie_break_weights_are_distinct():
    weights = list(KIND_PRIORITY.values())
    assert len(set(weights)) == len(weights)


def test_a_plan_is_produced(bundle_manifest, profile, directory_table):
    report = plan_for(bundle_manifest, profile, directory_table)
    assert len(report.plan) > 0
    assert report.plan.profile is profile


def test_the_redistributable_goes_first(bundle_manifest, profile, directory_table):
    order = plan_for(bundle_manifest, profile, directory_table).plan.step_ids
    assert order[0] == "vcredist"


def test_the_registry_block_goes_last(bundle_manifest, profile, directory_table):
    order = plan_for(bundle_manifest, profile, directory_table).plan.step_ids
    assert order[-1] == "reg.productinfo"


def test_the_assembly_precedes_its_policy(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("sxs.mfc") < order.index("sxs.mfc.policy")


def test_the_assemblies_precede_com_registration(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("sxs.mfc") < order.index("ocx.c1sizer")
    assert order.index("sxs.mfc.policy") < order.index("ocx.vsflex")


def test_the_driver_precedes_the_bitstream(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("driver.ftdi") < order.index("fpga.pcdc")


def test_the_font_precedes_the_template_set(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("font.ocrb") < order.index("templates.gs1")


def test_the_directory_precedes_its_contents(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("dir.templates") < order.index("templates.gs1")


def test_the_shortcut_follows_its_target(bundle_manifest, profile, directory_table):
    order = list(plan_for(bundle_manifest, profile, directory_table).plan.step_ids)
    assert order.index("file.v4t") < order.index("shortcut.desktop")


def test_language_packs_are_skipped_when_the_terminal_asked_for_none(
    bundle_manifest, profile, directory_table
):
    report = plan_for(bundle_manifest, profile, directory_table)
    assert "lang.polish" not in report.plan
    assert "lang.russian" not in report.plan
    assert "lang.polish" in report.skipped


def test_a_selected_language_pack_is_planned(bundle_manifest, profile, directory_table):
    polish = profile.with_languages(["pl-PL"])
    report = plan_for(bundle_manifest, polish, directory_table)
    assert "lang.polish" in report.plan
    assert "lang.russian" not in report.plan


def test_the_language_pack_follows_the_executable(bundle_manifest, profile, directory_table):
    polish = profile.with_languages(["pl-PL"])
    order = list(plan_for(bundle_manifest, polish, directory_table).plan.step_ids)
    assert order.index("file.v4t") < order.index("lang.polish")


def test_a_mini_gets_no_bitstream(bundle_manifest, profile_mini, directory_table):
    report = plan_for(bundle_manifest, profile_mini, directory_table)
    assert "fpga.pcdc" not in report.plan
    assert "only for" in report.skipped["fpga.pcdc"]


def test_a_mini_plan_still_stages_the_driver(bundle_manifest, profile_mini, directory_table):
    # The bridge is still how the head is driven, there is just no bitstream.
    assert "driver.ftdi" in plan_for(bundle_manifest, profile_mini, directory_table).plan


def test_steps_carry_resolved_targets(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert plan.require("file.v4t").target == r"C:\Sellenis\S-jet600\V4T.exe"
    assert plan.require("fpga.pcdc").target == r"C:\Sellenis\S-jet600\FpgaImage\pcdc_top.rbf"


def test_a_directory_step_targets_the_directory_itself(
    bundle_manifest, profile, directory_table
):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert plan.require("dir.templates").target == r"C:\Sellenis\S-jet600\Templates"


def test_the_ocx_resolves_to_syswow64_on_x64(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert plan.require("ocx.c1sizer").target == r"C:\Windows\SysWOW64\C1Sizer.ocx"


def test_the_ocx_resolves_to_system32_on_x86(bundle_manifest, profile_x86, directory_table):
    plan = plan_for(bundle_manifest, profile_x86, directory_table).plan
    assert plan.require("ocx.c1sizer").target == r"C:\Windows\System32\C1Sizer.ocx"


def test_a_native_item_is_not_redirected(profile, directory_table):
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="ocx.native",
                kind=PayloadKind.COM_SERVER,
                name="C1Sizer.ocx",
                directory="SystemFolder",
                source=__import__("pathlib").PurePosixPath("payload/C1Sizer.ocx"),
                detail={"native": True},
            )
        ]
    )
    plan = plan_for(manifest, profile, directory_table).plan
    assert plan.require("ocx.native").target == r"C:\Windows\System32\C1Sizer.ocx"


def test_steps_without_a_directory_have_no_target(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert plan.require("driver.ftdi").target is None
    assert plan.require("reg.productinfo").target is None


def test_step_kinds_follow_the_payload_kinds(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert plan.require("file.v4t").kind is StepKind.COPY_FILE
    assert plan.require("fpga.pcdc").kind is StepKind.PROGRAM_FPGA
    assert plan.require("ocx.c1sizer").kind is StepKind.REGISTER_COM
    assert plan.require("reg.productinfo").kind is StepKind.WRITE_REGISTRY


def test_the_bitstream_step_is_irreversible(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert [s.step_id for s in plan.irreversible_steps] == ["vcredist", "fpga.pcdc"]


def test_the_plan_is_reproducible(bundle_manifest, profile, directory_table):
    first = plan_for(bundle_manifest, profile, directory_table).plan
    second = plan_for(bundle_manifest, profile, directory_table).plan
    assert first.step_ids == second.step_ids
    assert first.digest() == second.digest()


def test_declaring_the_manifest_in_a_different_order_plans_the_same(
    bundle_manifest, profile, directory_table
):
    shuffled = Manifest(items=list(reversed(bundle_manifest.items)))
    assert (
        plan_for(shuffled, profile, directory_table).plan.digest()
        == plan_for(bundle_manifest, profile, directory_table).plan.digest()
    )


def test_two_terminals_of_the_same_shape_share_a_plan_digest(
    bundle_manifest, profile, directory_table
):
    other = profile.model_copy(update={"terminal_id": "LINE-PC-07"})
    assert (
        plan_for(bundle_manifest, other, directory_table).plan.digest()
        == plan_for(bundle_manifest, profile, directory_table).plan.digest()
    )


def test_a_mini_plan_has_a_different_digest(bundle_manifest, profile, profile_mini, directory_table):
    assert (
        plan_for(bundle_manifest, profile_mini, directory_table).plan.digest()
        != plan_for(bundle_manifest, profile, directory_table).plan.digest()
    )


def test_warnings_carry_the_skipped_items(bundle_manifest, profile, directory_table):
    plan = plan_for(bundle_manifest, profile, directory_table).plan
    assert any("lang.polish" in warning for warning in plan.warnings)


def test_the_report_flags_warnings(bundle_manifest, profile, directory_table):
    assert plan_for(bundle_manifest, profile, directory_table).has_warnings


def test_a_bundle_with_nothing_skipped_has_no_warnings(profile, directory_table):
    manifest = Manifest(items=[PayloadItem(item_id="reg", kind=PayloadKind.REGISTRY, name="r")])
    assert not plan_for(manifest, profile, directory_table).has_warnings


def test_missing_requirements_are_reported(profile, directory_table):
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="ocx",
                kind=PayloadKind.COM_SERVER,
                name="C1Sizer.ocx",
                directory="SystemFolder",
                source=__import__("pathlib").PurePosixPath("payload/C1Sizer.ocx"),
                requires=("vc80.mfc",),
            )
        ]
    )
    report = plan_for(manifest, profile, directory_table)
    assert report.missing_requirements == {"ocx": ["vc80.mfc"]}
    assert report.has_warnings


def test_report_lines_read_as_a_summary(bundle_manifest, profile, directory_table):
    lines = plan_for(bundle_manifest, profile, directory_table).lines()
    assert lines[0].startswith("LINE-PC-04")
    assert any(line.startswith("skipped lang.polish") for line in lines)


def test_an_invalid_manifest_is_refused(profile, directory_table):
    manifest = Manifest(product="")
    with pytest.raises(ManifestError, match="no product name"):
        plan_for(manifest, profile, directory_table)


def test_an_unresolvable_directory_is_refused(profile, directory_table):
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="lost",
                kind=PayloadKind.FILE,
                name="x.dll",
                directory="Ghost_Dir",
                source=__import__("pathlib").PurePosixPath("payload/x.dll"),
            )
        ]
    )
    with pytest.raises(DirectoryResolutionError):
        plan_for(manifest, profile, directory_table)


def test_a_rule_that_creates_a_cycle_is_reported(bundle_manifest, profile, directory_table):
    class Loop(OrderingRule):
        name = "loop"

        def edges(self, context):
            return [("file.v4t", "vcredist", "made up"), ("vcredist", "file.v4t", "made up")]

    with pytest.raises(PlanCycleError):
        plan_for(bundle_manifest, profile, directory_table, rules=(Loop(),))


def test_build_graph_ignores_edges_to_items_that_were_filtered_out(
    bundle_manifest, profile_mini
):
    from stavewright.model.payload import select

    applicable = select(bundle_manifest.items, profile_mini)
    graph = build_graph(applicable)
    assert "fpga.pcdc" not in graph
    # The driver still precedes the registry block, it just no longer precedes a
    # bitstream that is not being installed.
    assert "fpga.pcdc" not in graph.successors("driver.ftdi")
    assert graph.successors("driver.ftdi") == ["reg.productinfo"]


def test_stamping_can_be_turned_off(bundle_manifest, profile, directory_table):
    assert plan_for(bundle_manifest, profile, directory_table).plan.created_at is None


def test_stamping_is_on_by_default(bundle_manifest, profile, directory_table):
    report = build_plan(bundle_manifest, profile, directory_table)
    assert report.plan.created_at is not None


def test_explain_order_covers_every_step(bundle_manifest, profile, directory_table):
    from stavewright.model.payload import select

    plan = plan_for(bundle_manifest, profile, directory_table).plan
    graph = build_graph(select(bundle_manifest.items, profile))
    lines = explain_order(plan, graph)
    assert len(lines) == len(plan)
    assert lines[0].endswith("(nothing requires it to be later)")


def test_explain_order_names_the_reason(bundle_manifest, profile, directory_table):
    from stavewright.model.payload import select

    plan = plan_for(bundle_manifest, profile, directory_table).plan
    graph = build_graph(select(bundle_manifest.items, profile))
    lines = explain_order(plan, graph)
    fpga_line = next(line for line in lines if "fpga.pcdc" in line)
    assert "enumerate" in fpga_line
