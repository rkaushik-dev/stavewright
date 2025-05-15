from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from v4t_setup.model.payload import PayloadItem, PayloadKind
from v4t_setup.plan.rules import (
    DEFAULT_RULES,
    AssemblyBeforeComRule,
    AssemblyBeforePolicyRule,
    DirectoryBeforeContentsRule,
    DriverBeforeFpgaRule,
    ExecutableBeforeLanguageRule,
    ExplicitTagRule,
    FontBeforeTemplateRule,
    RedistributableRule,
    RegistryLastRule,
    RuleContext,
    ShortcutAfterTargetRule,
    collect_edges,
    unsatisfied_requirements,
)

NEEDS_DIR = {"file", "directory", "com-server", "fpga-image", "template-set", "language-pack"}
NEEDS_SRC = {
    "file",
    "com-server",
    "sxs-assembly",
    "sxs-policy",
    "driver-package",
    "fpga-image",
    "font",
    "template-set",
    "language-pack",
    "redistributable",
}


def item(item_id: str, kind: PayloadKind, name: str | None = None, **kwargs) -> PayloadItem:
    kwargs.setdefault("name", name or item_id)
    if kind.value in NEEDS_DIR:
        kwargs.setdefault("directory", "APPDIR")
    if kind.value in NEEDS_SRC:
        kwargs.setdefault("source", PurePosixPath(f"payload/{item_id}"))
    return PayloadItem(item_id=item_id, kind=kind, **kwargs)


def pairs(edges) -> set[tuple[str, str]]:
    return {(before, after) for before, after, _ in edges}


def context(*items: PayloadItem) -> RuleContext:
    return RuleContext(items=items)


def test_explicit_tags_produce_an_edge():
    edges = ExplicitTagRule().edges(
        context(
            item("crt", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
            item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt",)),
        )
    )
    assert pairs(edges) == {("crt", "ocx")}
    assert edges[0][2] == "needs vc80.crt"


def test_a_tag_with_several_providers_produces_an_edge_from_each():
    edges = ExplicitTagRule().edges(
        context(
            item("crt.a", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
            item("crt.b", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
            item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt",)),
        )
    )
    assert pairs(edges) == {("crt.a", "ocx"), ("crt.b", "ocx")}


def test_a_tag_nothing_provides_produces_no_edge():
    edges = ExplicitTagRule().edges(
        context(item("ocx", PayloadKind.COM_SERVER, requires=("nothing",)))
    )
    assert edges == []


def test_an_item_that_provides_and_requires_the_same_tag_does_not_self_edge():
    edges = ExplicitTagRule().edges(
        context(item("odd", PayloadKind.FILE, provides=("x",), requires=("x",)))
    )
    assert edges == []


def test_the_redistributable_goes_first():
    edges = RedistributableRule().edges(
        context(
            item("vcredist", PayloadKind.REDISTRIBUTABLE),
            item("crt", PayloadKind.SXS_ASSEMBLY),
            item("ocx", PayloadKind.COM_SERVER),
            item("exe", PayloadKind.FILE, name="V4T.exe"),
        )
    )
    assert pairs(edges) == {("vcredist", "crt"), ("vcredist", "ocx"), ("vcredist", "exe")}


def test_no_redistributable_means_no_edges():
    edges = RedistributableRule().edges(context(item("ocx", PayloadKind.COM_SERVER)))
    assert edges == []


def test_the_redistributable_does_not_order_a_registry_write():
    edges = RedistributableRule().edges(
        context(
            item("vcredist", PayloadKind.REDISTRIBUTABLE),
            item("reg", PayloadKind.REGISTRY),
        )
    )
    assert edges == []


def test_an_assembly_goes_on_before_its_policy():
    edges = AssemblyBeforePolicyRule().edges(
        context(
            item("crt", PayloadKind.SXS_ASSEMBLY),
            item("crt.policy", PayloadKind.SXS_POLICY),
        )
    )
    assert pairs(edges) == {("crt", "crt.policy")}
    assert "not there yet" in edges[0][2]


def test_assemblies_go_on_before_com_registration():
    edges = AssemblyBeforeComRule().edges(
        context(
            item("crt", PayloadKind.SXS_ASSEMBLY),
            item("crt.policy", PayloadKind.SXS_POLICY),
            item("c1sizer", PayloadKind.COM_SERVER),
            item("vsflex", PayloadKind.COM_SERVER),
        )
    )
    assert pairs(edges) == {
        ("crt", "c1sizer"),
        ("crt", "vsflex"),
        ("crt.policy", "c1sizer"),
        ("crt.policy", "vsflex"),
    }


def test_the_com_rule_names_the_error_code():
    edges = AssemblyBeforeComRule().edges(
        context(item("crt", PayloadKind.SXS_ASSEMBLY), item("ocx", PayloadKind.COM_SERVER))
    )
    assert "0x8007007E" in edges[0][2]


def test_a_directory_goes_on_before_its_contents():
    edges = DirectoryBeforeContentsRule().edges(
        context(
            item("dir.templates", PayloadKind.DIRECTORY, directory="Templates_Dir"),
            item("tpl", PayloadKind.TEMPLATE_SET, directory="Templates_Dir"),
            item("elsewhere", PayloadKind.FILE, directory="APPDIR"),
        )
    )
    assert pairs(edges) == {("dir.templates", "tpl")}


def test_a_directory_item_does_not_order_itself():
    edges = DirectoryBeforeContentsRule().edges(
        context(item("dir", PayloadKind.DIRECTORY, directory="Templates_Dir"))
    )
    assert edges == []


def test_items_without_a_directory_are_left_alone():
    edges = DirectoryBeforeContentsRule().edges(
        context(
            item("dir", PayloadKind.DIRECTORY, directory="APPDIR"),
            item(
                "ftdi",
                PayloadKind.DRIVER_PACKAGE,
                source=PurePosixPath("payload/ftdimfr"),
            ),
        )
    )
    assert edges == []


def test_the_driver_goes_on_before_the_bitstream():
    edges = DriverBeforeFpgaRule().edges(
        context(
            item("ftdi", PayloadKind.DRIVER_PACKAGE),
            item("fpga", PayloadKind.FPGA_IMAGE, directory="FpgaImage_Dir"),
        )
    )
    assert pairs(edges) == {("ftdi", "fpga")}
    assert "enumerate" in edges[0][2]


def test_no_bitstream_means_no_driver_ordering():
    edges = DriverBeforeFpgaRule().edges(context(item("ftdi", PayloadKind.DRIVER_PACKAGE)))
    assert edges == []


def test_fonts_go_on_before_templates():
    edges = FontBeforeTemplateRule().edges(
        context(
            item("ocrb", PayloadKind.FONT),
            item("templates", PayloadKind.TEMPLATE_SET, directory="Templates_Dir"),
        )
    )
    assert pairs(edges) == {("ocrb", "templates")}


def test_the_executable_goes_on_before_language_packs():
    edges = ExecutableBeforeLanguageRule().edges(
        context(
            item("exe", PayloadKind.FILE, name="V4T.exe"),
            item("dll", PayloadKind.FILE, name="qu_imager.dll"),
            item("lang.pl", PayloadKind.LANGUAGE_PACK, name="LangPolish.dll"),
        )
    )
    assert pairs(edges) == {("exe", "lang.pl")}


def test_the_executable_match_is_case_insensitive():
    edges = ExecutableBeforeLanguageRule().edges(
        context(
            item("exe", PayloadKind.FILE, name="V4T.EXE"),
            item("lang.pl", PayloadKind.LANGUAGE_PACK, name="LangPolish.dll"),
        )
    )
    assert pairs(edges) == {("exe", "lang.pl")}


def test_a_shortcut_goes_on_after_its_target():
    edges = ShortcutAfterTargetRule().edges(
        context(
            item("exe", PayloadKind.FILE, name="V4T.exe"),
            item("shortcut", PayloadKind.SHORTCUT, detail={"target": "V4T.exe"}),
        )
    )
    assert pairs(edges) == {("exe", "shortcut")}


def test_a_shortcut_pointing_at_nothing_in_the_bundle_is_left_alone():
    edges = ShortcutAfterTargetRule().edges(
        context(item("shortcut", PayloadKind.SHORTCUT, detail={"target": "Ghost.exe"}))
    )
    assert edges == []


def test_a_shortcut_with_no_target_is_left_alone():
    edges = ShortcutAfterTargetRule().edges(
        context(item("shortcut", PayloadKind.SHORTCUT))
    )
    assert edges == []


def test_the_registry_block_goes_on_last():
    edges = RegistryLastRule().edges(
        context(
            item("exe", PayloadKind.FILE, name="V4T.exe"),
            item("fpga", PayloadKind.FPGA_IMAGE, directory="FpgaImage_Dir"),
            item("reg", PayloadKind.REGISTRY),
        )
    )
    assert pairs(edges) == {("exe", "reg"), ("fpga", "reg")}


def test_registry_items_do_not_order_each_other():
    edges = RegistryLastRule().edges(
        context(item("reg.a", PayloadKind.REGISTRY), item("reg.b", PayloadKind.REGISTRY))
    )
    assert edges == []


def test_no_registry_means_no_edges():
    edges = RegistryLastRule().edges(context(item("exe", PayloadKind.FILE, name="V4T.exe")))
    assert edges == []


def test_collect_edges_runs_every_rule():
    items = [
        item("vcredist", PayloadKind.REDISTRIBUTABLE),
        item("crt", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
        item("crt.policy", PayloadKind.SXS_POLICY),
        item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt",)),
        item("reg", PayloadKind.REGISTRY),
    ]
    found = pairs(collect_edges(items))
    assert ("vcredist", "crt") in found
    assert ("crt", "crt.policy") in found
    assert ("crt", "ocx") in found
    assert ("ocx", "reg") in found


def test_collect_edges_with_a_narrowed_rule_set():
    items = [
        item("vcredist", PayloadKind.REDISTRIBUTABLE),
        item("ocx", PayloadKind.COM_SERVER),
    ]
    assert collect_edges(items, rules=(RegistryLastRule(),)) == []


def test_collect_edges_of_nothing_is_empty():
    assert collect_edges([]) == []


def test_every_default_rule_has_a_distinct_name():
    names = [rule.name for rule in DEFAULT_RULES]
    assert len(set(names)) == len(names)


def test_every_edge_carries_a_reason():
    items = [
        item("vcredist", PayloadKind.REDISTRIBUTABLE),
        item("crt", PayloadKind.SXS_ASSEMBLY),
        item("ocx", PayloadKind.COM_SERVER),
        item("reg", PayloadKind.REGISTRY),
    ]
    for _, _, reason in collect_edges(items):
        assert reason


def test_unsatisfied_requirements_reports_the_gap():
    items = [item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt", "vc80.mfc"))]
    assert unsatisfied_requirements(items) == {"ocx": ["vc80.crt", "vc80.mfc"]}


def test_unsatisfied_requirements_is_empty_when_everything_is_provided():
    items = [
        item("crt", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
        item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt",)),
    ]
    assert unsatisfied_requirements(items) == {}


def test_unsatisfied_requirements_reports_only_the_missing_ones():
    items = [
        item("crt", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
        item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt", "vc80.mfc")),
    ]
    assert unsatisfied_requirements(items) == {"ocx": ["vc80.mfc"]}


def test_rule_context_lookups():
    ctx = context(
        item("crt", PayloadKind.SXS_ASSEMBLY, provides=("vc80.crt",)),
        item("ocx", PayloadKind.COM_SERVER, requires=("vc80.crt",)),
    )
    assert [i.item_id for i in ctx.of_kind(PayloadKind.SXS_ASSEMBLY)] == ["crt"]
    assert [i.item_id for i in ctx.providing("vc80.crt")] == ["crt"]
    assert [i.item_id for i in ctx.requiring("vc80.crt")] == ["ocx"]
    assert ctx.all_provided_tags == {"vc80.crt"}
    assert ctx.all_required_tags == {"vc80.crt"}


def test_the_base_rule_class_is_abstract():
    from v4t_setup.plan.rules import OrderingRule

    with pytest.raises(NotImplementedError):
        OrderingRule().edges(context())
