from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from v4t_setup.aip.builder import (
    COM_COMPONENTS,
    FPGA_MACHINES,
    NATIVE_COMPONENTS,
    build_manifest,
    bundle_source,
    language_for,
)
from v4t_setup.aip.normalize import normalise
from v4t_setup.aip.reader import read_aip
from v4t_setup.errors import ManifestError
from v4t_setup.model.payload import PayloadKind
from v4t_setup.model.profile import Architecture


@pytest.fixture(scope="module")
def project(request):
    return normalise(read_aip(request.path.parent.parent.parent / "setup.aip"))


@pytest.fixture(scope="module")
def repo_root(request) -> Path:
    return request.path.parent.parent.parent


@pytest.fixture(scope="module")
def built(project, repo_root):
    return build_manifest(project, repo_root)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../Release/V4T.exe", "payload/Release/V4T.exe"),
        ("Matricode/Quattro/Templates/GS1-1.txt", "payload/Matricode/Quattro/Templates/GS1-1.txt"),
        ("../../deeper/x.dll", "payload/deeper/x.dll"),
        ("./local.dll", "payload/local.dll"),
        ("plain.dll", "payload/plain.dll"),
    ],
)
def test_source_paths_are_flattened_into_the_bundle(given, expected):
    assert bundle_source(PurePosixPath(given)) == PurePosixPath(expected)


def test_a_bundle_source_never_climbs_out():
    result = bundle_source(PurePosixPath("../../../../etc/passwd"))
    assert ".." not in result.parts
    assert result.parts[0] == "payload"


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("LangPolish.dll", "LangPolish"),
        ("LangRussian.dll", "LangRussian"),
        ("LANGPOLISH.DLL", None),
        ("V4T.exe", None),
        ("cximage.dll", None),
    ],
)
def test_language_detection(component, expected):
    assert language_for(component) == expected


def test_the_shipped_project_builds_a_manifest(built):
    assert len(built.manifest) == 58
    built.manifest.validate()


def test_the_manifest_carries_the_product_identity(built, project):
    assert built.manifest.product == project.product_name
    assert built.manifest.upgrade_code == project.upgrade_code
    assert built.manifest.version == project.product_version


def test_the_kinds_come_out_the_way_the_package_is_shaped(built):
    counts: dict[str, int] = {}
    for item in built.manifest:
        counts[item.kind.value] = counts.get(item.kind.value, 0) + 1
    assert counts == {
        "registry": 8,
        "com-server": 4,
        "directory": 15,
        "file": 21,
        "language-pack": 8,
        "font": 1,
        "fpga-image": 1,
    }


def test_every_language_pack_is_gated_on_its_locale(built):
    packs = built.manifest.of_kind(PayloadKind.LANGUAGE_PACK)
    assert len(packs) == 8
    for pack in packs:
        assert len(pack.language_ids) == 1
    assert {next(iter(pack.language_ids)) for pack in packs} == {
        1031, 1036, 1045, 1046, 1049, 1055, 2052, 3082
    }


def test_the_polish_pack_is_the_one_you_would_expect(built):
    polish = built.manifest.require("LangPolish.dll")
    assert polish.kind is PayloadKind.LANGUAGE_PACK
    assert polish.language_ids == frozenset({1045})
    assert polish.directory == "APPDIR"


def test_the_ocx_pair_comes_out_as_com_servers(built):
    servers = built.manifest.of_kind(PayloadKind.COM_SERVER)
    assert {item.name for item in servers} == {"C1Sizer.ocx", "vsFlex8n.ocx"}
    assert len(servers) == 4


def test_the_native_half_of_the_pair_is_marked_x64(built):
    native = [item for item in built.manifest.of_kind(PayloadKind.COM_SERVER)
              if item.architecture is Architecture.X64]
    assert len(native) == 2
    for item in native:
        assert item.component in NATIVE_COMPONENTS
        assert item.detail.get("native") is True


def test_the_wow_half_is_not_architecture_gated(built):
    others = [item for item in built.manifest.of_kind(PayloadKind.COM_SERVER)
              if item.architecture is None]
    assert len(others) == 2
    for item in others:
        assert item.component in COM_COMPONENTS
        assert "native" not in item.detail


def test_the_bitstream_is_gated_on_the_machines_that_have_a_controller(built):
    fpga = built.manifest.of_kind(PayloadKind.FPGA_IMAGE)
    assert len(fpga) == 1
    assert fpga[0].machines == FPGA_MACHINES
    assert fpga[0].directory == "FpgaImage_Dir"


def test_the_font_carries_its_name(built):
    fonts = built.manifest.of_kind(PayloadKind.FONT)
    assert len(fonts) == 1
    assert fonts[0].detail["font"] == "OCR-B"


def test_registry_only_components_become_registry_items(built):
    registry = built.manifest.of_kind(PayloadKind.REGISTRY)
    assert any(item.component == "ProductInformation" for item in registry)
    for item in registry:
        assert item.source is None
        assert item.detail["rows"]


def test_components_with_neither_files_nor_registry_become_directories(built):
    directories = built.manifest.of_kind(PayloadKind.DIRECTORY)
    assert any(item.component == "Templates_Dir" or item.directory for item in directories)
    for item in directories:
        assert item.source is None
        assert item.directory


def test_every_source_is_inside_the_bundle(built):
    for item in built.manifest:
        if item.source is None:
            continue
        assert item.source.parts[0] == "payload"
        assert ".." not in item.source.parts


def test_the_original_aip_source_is_kept_for_traceability(built):
    item = built.manifest.require("GS11.txt")
    assert item.detail["aip_source"] == "Matricode/Quattro/Templates/GS1-1.txt"


def test_what_is_in_the_repository_gets_digested(built):
    # Six payload files actually live in this repository. The rest are built
    # binaries that come out of ../Release, which is not here.
    assert built.digested == 6
    assert built.manifest.require("GS11.txt").digest is not None
    assert built.manifest.require("pcdc_top.rbf").digest is not None


def test_what_is_not_in_the_repository_is_reported(built):
    assert not built.is_complete
    assert "LangPolish.dll" in built.missing_sources
    assert built.missing_sources["LangPolish.dll"] == "../Release/LangPolish.dll"


def test_the_report_lines_name_what_is_missing(built):
    lines = built.lines()
    assert lines[1].endswith("items digested")
    assert any("no payload for LangPolish.dll" in line for line in lines)


def test_skipping_the_digests_is_quicker_and_says_nothing_is_missing(project, repo_root):
    report = build_manifest(project, repo_root, digest_payload=False)
    assert report.digested == 0
    assert report.is_complete
    assert all(item.digest is None for item in report.manifest)


def test_a_higher_install_level_is_the_same_here(project, repo_root):
    # The shipped project has one feature at level 1, so nothing changes.
    low = build_manifest(project, repo_root, install_level=100, digest_payload=False)
    high = build_manifest(project, repo_root, install_level=1000, digest_payload=False)
    assert len(low.manifest) == len(high.manifest)


def test_an_install_level_that_selects_nothing_is_refused(project, repo_root):
    with pytest.raises(ManifestError, match="no components are selected"):
        build_manifest(project, repo_root, install_level=0, digest_payload=False)


def test_notes_reach_the_manifest(project, repo_root):
    report = build_manifest(project, repo_root, digest_payload=False, notes="bench build")
    assert report.manifest.notes == "bench build"


def test_the_manifest_is_stamped(project, repo_root):
    report = build_manifest(project, repo_root, digest_payload=False)
    assert report.manifest.generated_at is not None


def test_the_built_manifest_round_trips(project, repo_root):
    from v4t_setup.model.manifest import loads

    report = build_manifest(project, repo_root, digest_payload=False)
    assert loads(report.manifest.dumps()).to_dict() == report.manifest.to_dict()


def test_the_built_manifest_plans(project, repo_root, profile, directory_table):
    from v4t_setup.plan.planner import build_plan

    report = build_manifest(project, repo_root, digest_payload=False)
    planned = build_plan(report.manifest, profile, project.directories, stamp=False)
    assert len(planned.plan) > 0
    assert "pcdc_top.rbf" in planned.plan


def test_a_mini_plan_from_the_built_manifest_drops_the_bitstream(
    project, repo_root, profile_mini
):
    from v4t_setup.plan.planner import build_plan

    report = build_manifest(project, repo_root, digest_payload=False)
    planned = build_plan(report.manifest, profile_mini, project.directories, stamp=False)
    assert "pcdc_top.rbf" not in planned.plan
    assert "pcdc_top.rbf" in planned.skipped


def test_a_polish_terminal_gets_only_the_polish_pack(project, repo_root, profile):
    from v4t_setup.plan.planner import build_plan

    report = build_manifest(project, repo_root, digest_payload=False)
    polish = profile.with_languages(["pl-PL"])
    planned = build_plan(report.manifest, polish, project.directories, stamp=False)
    assert "LangPolish.dll" in planned.plan
    assert "LangRussian.dll" not in planned.plan
