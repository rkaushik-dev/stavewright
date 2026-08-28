from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from stavewright.errors import ManifestError
from stavewright.model.payload import (
    PayloadItem,
    PayloadKind,
    by_kind,
    duplicate_ids,
    rejected,
    select,
)
from stavewright.model.profile import Architecture, TerminalModel


def item(item_id: str, kind: PayloadKind = PayloadKind.FILE, **kwargs) -> PayloadItem:
    kwargs.setdefault("name", item_id)
    if kind.value in {"file", "com-server", "fpga-image", "template-set", "language-pack"}:
        kwargs.setdefault("directory", "APPDIR")
        kwargs.setdefault("source", PurePosixPath(f"payload/{item_id}"))
    return PayloadItem(item_id=item_id, kind=kind, **kwargs)


def test_an_item_needs_an_id():
    with pytest.raises(ManifestError, match="item id must not be empty"):
        PayloadItem(item_id="", kind=PayloadKind.REGISTRY, name="x")


def test_an_item_needs_a_name():
    with pytest.raises(ManifestError, match="must have a name"):
        PayloadItem(item_id="one", kind=PayloadKind.REGISTRY, name="")


@pytest.mark.parametrize(
    "kind",
    [
        PayloadKind.FILE,
        PayloadKind.COM_SERVER,
        PayloadKind.DRIVER_PACKAGE,
        PayloadKind.FPGA_IMAGE,
        PayloadKind.FONT,
        PayloadKind.REDISTRIBUTABLE,
    ],
)
def test_kinds_that_copy_bytes_must_name_a_source(kind):
    with pytest.raises(ManifestError, match="must name a source"):
        PayloadItem(item_id="one", kind=kind, name="x", directory="APPDIR")


@pytest.mark.parametrize("kind", [PayloadKind.REGISTRY, PayloadKind.SHORTCUT])
def test_kinds_that_do_not_copy_bytes_need_no_source(kind):
    assert PayloadItem(item_id="one", kind=kind, name="x").source is None


@pytest.mark.parametrize(
    "kind",
    [PayloadKind.FILE, PayloadKind.DIRECTORY, PayloadKind.COM_SERVER, PayloadKind.FPGA_IMAGE],
)
def test_kinds_that_land_somewhere_must_name_a_directory(kind):
    with pytest.raises(ManifestError, match="must name a directory"):
        PayloadItem(
            item_id="one", kind=kind, name="x", source=PurePosixPath("payload/x")
        )


def test_a_driver_package_needs_no_directory():
    # It goes into the driver store, not a directory the table can resolve.
    assert PayloadItem(
        item_id="ftdi",
        kind=PayloadKind.DRIVER_PACKAGE,
        name="ftdimfr",
        source=PurePosixPath("ftdimfr"),
    ).directory is None


def test_an_unconstrained_item_is_universal():
    assert item("V4T.exe").is_universal


def test_an_architecture_constraint_makes_it_not_universal():
    assert not item("C1Sizer.ocx", architecture=Architecture.X86).is_universal


def test_a_machine_constraint_makes_it_not_universal():
    assert not item("pcdc_top.rbf", machines=frozenset({TerminalModel.SJET_600})).is_universal


def test_a_language_constraint_makes_it_not_universal():
    assert not item("LangPolish.dll", language_ids=frozenset({1045})).is_universal


def test_a_universal_item_applies_everywhere(profile, profile_x86, profile_mini):
    universal = item("V4T.exe")
    for candidate in (profile, profile_x86, profile_mini):
        assert universal.applies_to(candidate)
        assert universal.excluded_because(candidate) is None


def test_an_architecture_constrained_item(profile, profile_x86):
    x64_only = item("native.dll", architecture=Architecture.X64)
    assert x64_only.applies_to(profile)
    assert not x64_only.applies_to(profile_x86)
    assert "terminal is x86" in x64_only.excluded_because(profile_x86)


def test_the_bitstream_does_not_go_onto_a_mini(profile, profile_mini):
    bitstream = item(
        "pcdc_top.rbf",
        kind=PayloadKind.FPGA_IMAGE,
        directory="FpgaImage_Dir",
        machines=frozenset({TerminalModel.SJET_600, TerminalModel.SJET_600_TIJ}),
    )
    assert bitstream.applies_to(profile)
    assert not bitstream.applies_to(profile_mini)
    assert "only for" in bitstream.excluded_because(profile_mini)


def test_a_language_pack_follows_the_profile(profile):
    polish = item(
        "LangPolish.dll",
        kind=PayloadKind.LANGUAGE_PACK,
        language_ids=frozenset({1045}),
    )
    assert not polish.applies_to(profile)
    assert polish.applies_to(profile.with_languages(["pl-PL"]))


def test_a_language_pack_serving_several_lcids_applies_if_any_match(profile):
    western = item(
        "LangWestern.dll",
        kind=PayloadKind.LANGUAGE_PACK,
        language_ids=frozenset({1031, 1036, 3082}),
    )
    assert western.applies_to(profile.with_languages(["fr-FR"]))
    assert not western.applies_to(profile.with_languages(["pl-PL"]))


def test_the_exclusion_reason_names_the_language(profile):
    polish = item(
        "LangPolish.dll", kind=PayloadKind.LANGUAGE_PACK, language_ids=frozenset({1045})
    )
    assert "1045" in polish.excluded_because(profile)


def test_architecture_is_checked_before_machine(profile_mini):
    both = item(
        "x",
        architecture=Architecture.X86,
        machines=frozenset({TerminalModel.SJET_600}),
    )
    assert "terminal is x64" in both.excluded_because(profile_mini)


def test_select_keeps_the_given_order(profile):
    items = [
        item("a"),
        item("b", architecture=Architecture.X86),
        item("c"),
    ]
    assert [i.item_id for i in select(items, profile)] == ["a", "c"]


def test_select_of_nothing_is_empty(profile):
    assert select([], profile) == []


def test_rejected_explains_each_drop(profile):
    items = [
        item("a"),
        item("b", architecture=Architecture.X86),
        item("c", machines=frozenset({TerminalModel.SJET_MINI})),
    ]
    reasons = rejected(items, profile)
    assert set(reasons) == {"b", "c"}
    assert "x86" in reasons["b"]
    assert "s-jet-mini" in reasons["c"]


def test_rejected_is_empty_when_everything_applies(profile):
    assert rejected([item("a"), item("b")], profile) == {}


def test_by_kind_groups_and_sorts_the_keys():
    grouped = by_kind(
        [
            item("a", PayloadKind.FILE),
            item("reg", PayloadKind.REGISTRY),
            item("b", PayloadKind.FILE),
        ]
    )
    assert list(grouped) == ["file", "registry"]
    assert [i.item_id for i in grouped["file"]] == ["a", "b"]


def test_by_kind_of_nothing_is_empty():
    assert by_kind([]) == {}


def test_duplicate_ids_are_found():
    assert duplicate_ids([item("a"), item("b"), item("a")]) == ["a"]


def test_no_duplicates_when_every_id_is_distinct():
    assert duplicate_ids([item("a"), item("b")]) == []


def test_describe_names_the_directory():
    assert item("V4T.exe").describe() == "file V4T.exe into APPDIR"


def test_describe_without_a_directory():
    described = PayloadItem(
        item_id="ftdi",
        kind=PayloadKind.DRIVER_PACKAGE,
        name="ftdimfr",
        source=PurePosixPath("ftdimfr"),
    ).describe()
    assert described == "driver-package ftdimfr"
