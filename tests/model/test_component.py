from __future__ import annotations

import pytest

from stavewright.errors import ManifestError
from stavewright.model.component import (
    Component,
    ComponentAttributes,
    Feature,
    FeatureAttributes,
    FeatureTree,
)

# Real rows out of the MsiCompsComponent table.
V4T_EXE = Component(
    component="V4T.exe",
    component_id="{A6B2E442-5247-4D76-BEFE-7100B893FAA8}",
    directory="APPDIR",
    key_path="V4T.exe",
)
OCX_WOW = Component(
    component="C1Sizer.ocx",
    component_id="{8A83879F-9481-41D8-B182-E39D3411F11A}",
    directory="SystemFolder",
    key_path="C1Sizer.ocx_1",
)
OCX_NATIVE = Component(
    component="C1Sizer.ocx_1",
    component_id="{D6F80E5B-630B-475A-80E1-E479CA4E5C99}",
    directory="System64Folder",
    attributes=ComponentAttributes.SIXTY_FOUR_BIT,
    key_path="C1Sizer.ocx",
)
PRODUCT_INFO = Component(
    component="ProductInformation",
    component_id="{1F32B609-B83D-4795-BAF9-8F1FBEBF34A6}",
    directory="APPDIR",
    attributes=ComponentAttributes.REGISTRY_KEY_PATH,
    key_path="Version",
)
FPGA = Component(
    component="pcdc_top.rbf",
    component_id="{686D7AF8-B1C6-421D-B5CE-1071B3CF627C}",
    directory="FpgaImage_Dir",
    key_path="pcdc_top.rbf",
)


def test_a_file_component_has_no_special_flags():
    assert not V4T_EXE.is_registry_key_path
    assert not V4T_EXE.is_64bit
    assert not V4T_EXE.is_permanent
    assert not V4T_EXE.is_transitive
    assert not V4T_EXE.is_shared
    assert not V4T_EXE.never_overwrite


def test_registry_key_path_flag_is_read():
    assert PRODUCT_INFO.is_registry_key_path
    assert PRODUCT_INFO.key_path == "Version"


def test_the_native_ocx_is_flagged_64_bit_and_the_wow_one_is_not():
    assert OCX_NATIVE.is_64bit
    assert not OCX_WOW.is_64bit


def test_combined_flags_are_all_visible():
    component = Component(
        component="msvcr80.dll",
        component_id="{6096F0B8-19DE-4935-B957-59052013F630}",
        directory="SystemFolder",
        attributes=(
            ComponentAttributes.PERMANENT
            | ComponentAttributes.SHARED_DLL_REF_COUNT
            | ComponentAttributes.NEVER_OVERWRITE
        ),
        key_path="msvcr80.dll",
    )
    assert component.is_permanent
    assert component.is_shared
    assert component.never_overwrite
    assert not component.is_64bit


def test_normalised_id_upper_cases():
    component = Component(
        component="x",
        component_id="{a6b2e442-5247-4d76-befe-7100b893faa8}",
        directory="APPDIR",
        key_path="x",
    )
    assert component.normalised_id == "{A6B2E442-5247-4D76-BEFE-7100B893FAA8}"


@pytest.mark.parametrize(
    "component_id",
    [
        "A6B2E442-5247-4D76-BEFE-7100B893FAA8",
        "{A6B2E442-5247-4D76-BEFE-7100B893FAA}",
        "{not-a-guid}",
        "",
        "{A6B2E442524 74D76BEFE7100B893FAA8}",
    ],
)
def test_a_malformed_component_id_is_refused(component_id):
    with pytest.raises(ManifestError, match="not a braced GUID"):
        Component(component="x", component_id=component_id, directory="APPDIR")


def test_an_empty_component_key_is_refused():
    with pytest.raises(ManifestError, match="component key must not be empty"):
        Component(
            component="",
            component_id="{A6B2E442-5247-4D76-BEFE-7100B893FAA8}",
            directory="APPDIR",
        )


def test_a_registry_key_path_component_must_name_its_value():
    with pytest.raises(ManifestError, match="names none"):
        Component(
            component="Version",
            component_id="{690BD647-2E82-4EB1-B909-DF3AE13860B9}",
            directory="APPDIR",
            attributes=ComponentAttributes.REGISTRY_KEY_PATH,
        )


def _tree() -> FeatureTree:
    return FeatureTree.from_features(
        [
            Feature(
                feature="MainApplication",
                title="S-Jet 600",
                level=1,
                display=1,
                components=("V4T.exe", "ProductInformation"),
            ),
            Feature(
                feature="Controls",
                title="OCX controls",
                parent="MainApplication",
                level=1,
                display=2,
                attributes=FeatureAttributes.FOLLOW_PARENT,
                components=("C1Sizer.ocx", "C1Sizer.ocx_1"),
            ),
            Feature(
                feature="Fpga",
                title="Controller bitstream",
                parent="MainApplication",
                level=1,
                display=3,
                components=("pcdc_top.rbf",),
            ),
            Feature(
                feature="SystemTest",
                title="System test templates",
                parent="MainApplication",
                level=200,
                display=4,
                components=("SystemTest",),
            ),
            Feature(
                feature="SystemTestData",
                parent="SystemTest",
                level=200,
                display=5,
                attributes=FeatureAttributes.FOLLOW_PARENT,
                components=("GS11.txt",),
            ),
        ]
    )


def test_roots_and_children_are_display_ordered():
    tree = _tree()
    assert tree.roots() == ["MainApplication"]
    assert tree.children_of("MainApplication") == ["Controls", "Fpga", "SystemTest"]
    assert tree.children_of("SystemTest") == ["SystemTestData"]
    assert tree.children_of("Fpga") == []


def test_ancestors_walk_up_to_the_root():
    assert _tree().ancestors_of("SystemTestData") == ["SystemTest", "MainApplication"]
    assert _tree().ancestors_of("MainApplication") == []


def test_duplicate_feature_keys_are_refused():
    with pytest.raises(ManifestError, match="appears twice"):
        FeatureTree.from_features([Feature(feature="A"), Feature(feature="A")])


def test_a_dangling_parent_is_refused():
    with pytest.raises(ManifestError, match="parent is not in the table"):
        FeatureTree.from_features([Feature(feature="Child", parent="Missing")])


def test_an_empty_feature_key_is_refused():
    with pytest.raises(ManifestError, match="feature key must not be empty"):
        Feature(feature="")


def test_a_negative_level_is_refused():
    with pytest.raises(ManifestError, match="must not be negative"):
        Feature(feature="A", level=-1)


def test_unknown_feature_lookup_reports_clearly():
    with pytest.raises(ManifestError, match="no such feature"):
        _tree()["Nope"]


def test_default_install_level_leaves_the_high_level_features_out():
    selected = _tree().selected(100)
    assert selected == {"MainApplication", "Controls", "Fpga"}


def test_raising_the_install_level_pulls_the_test_templates_in():
    selected = _tree().selected(200)
    assert "SystemTest" in selected
    assert "SystemTestData" in selected


def test_a_level_zero_feature_never_installs():
    tree = FeatureTree.from_features([Feature(feature="Never", level=0)])
    assert tree.selected(65535) == set()


def test_follow_parent_drags_a_child_in_even_at_a_higher_level():
    tree = FeatureTree.from_features(
        [
            Feature(feature="Parent", level=1),
            Feature(
                feature="Child",
                parent="Parent",
                level=1000,
                attributes=FeatureAttributes.FOLLOW_PARENT,
            ),
        ]
    )
    assert tree.selected(100) == {"Parent", "Child"}


def test_follow_parent_chains_all_the_way_down():
    tree = FeatureTree.from_features(
        [
            Feature(feature="A", level=1),
            Feature(feature="B", parent="A", level=900, attributes=FeatureAttributes.FOLLOW_PARENT),
            Feature(feature="C", parent="B", level=900, attributes=FeatureAttributes.FOLLOW_PARENT),
        ]
    )
    assert tree.selected(100) == {"A", "B", "C"}


def test_follow_parent_does_nothing_when_the_parent_is_out():
    tree = FeatureTree.from_features(
        [
            Feature(feature="A", level=900),
            Feature(feature="B", parent="A", level=900, attributes=FeatureAttributes.FOLLOW_PARENT),
        ]
    )
    assert tree.selected(100) == set()


def test_components_for_collects_across_selected_features():
    assert _tree().components_for(100) == {
        "V4T.exe",
        "ProductInformation",
        "C1Sizer.ocx",
        "C1Sizer.ocx_1",
        "pcdc_top.rbf",
    }


def test_components_for_grows_with_the_install_level():
    tree = _tree()
    assert tree.components_for(200) > tree.components_for(100)
    assert "GS11.txt" in tree.components_for(200)


def test_hidden_features_are_the_ones_with_display_zero():
    assert Feature(feature="A", display=0).is_hidden
    assert not Feature(feature="A", display=1).is_hidden


def test_orphan_components_are_reported():
    orphan = Component(
        component="Dlsbarunu.dll",
        component_id="{1A1D2610-8FBD-44BD-9D44-8051765BEBBE}",
        directory="APPDIR",
        key_path="Dlsbarunu.dll",
    )
    found = _tree().orphan_components([V4T_EXE, OCX_WOW, FPGA, orphan])
    assert found == ["Dlsbarunu.dll"]


def test_no_orphans_when_every_component_is_referenced():
    assert _tree().orphan_components([V4T_EXE, OCX_WOW, OCX_NATIVE, FPGA, PRODUCT_INFO]) == []
