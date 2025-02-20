from __future__ import annotations

import pytest

from v4t_setup.__about__ import UPGRADE_CODE
from v4t_setup.util.msiver import (
    InstalledProduct,
    MsiVersion,
    UpgradeAttributes,
    UpgradeRow,
    evaluate_upgrades,
)

SHIPPED = MsiVersion.parse("1.0.0")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", (1, 0, 0, None)),
        ("1.0", (1, 0, 0, None)),
        ("1.0.0", (1, 0, 0, None)),
        ("1.0.0.5", (1, 0, 0, 5)),
        ("255.255.65535", (255, 255, 65535, None)),
        ("  2.14.7  ", (2, 14, 7, None)),
    ],
)
def test_parse_accepts_the_legal_shapes(text, expected):
    version = MsiVersion.parse(text)
    assert (version.major, version.minor, version.build, version.extra) == expected


@pytest.mark.parametrize(
    "text",
    ["", "v1.0.0", "1.0.0.5.2", "1.0.0-rc1", "1..0", "abc", "1.0.0+meta"],
)
def test_parse_rejects_everything_else(text):
    with pytest.raises(ValueError, match="not an MSI product version"):
        MsiVersion.parse(text)


@pytest.mark.parametrize(
    ("major", "minor", "build"),
    [(256, 0, 0), (0, 256, 0), (0, 0, 65536), (-1, 0, 0)],
)
def test_field_limits_are_enforced(major, minor, build):
    with pytest.raises(ValueError, match="must be between"):
        MsiVersion(major=major, minor=minor, build=build)


def test_negative_fourth_field_is_rejected():
    with pytest.raises(ValueError, match="fourth field"):
        MsiVersion(1, 0, 0, extra=-1)


def test_str_round_trips():
    for text in ["1.0.0", "1.0.0.5", "12.3.400"]:
        assert str(MsiVersion.parse(text)) == text


def test_str_normalises_short_forms():
    assert str(MsiVersion.parse("2")) == "2.0.0"
    assert str(MsiVersion.parse("2.7")) == "2.7.0"


def test_the_fourth_field_is_invisible_to_comparison():
    a = MsiVersion.parse("1.0.0.4")
    b = MsiVersion.parse("1.0.0.5")
    assert a == b
    assert not a < b
    assert hash(a) == hash(b)


def test_differs_only_in_fourth_field_catches_the_classic_mistake():
    a = MsiVersion.parse("1.0.0.4")
    b = MsiVersion.parse("1.0.0.5")
    assert a.differs_only_in_fourth_field(b)
    # A missing fourth field counts as different from a present one, because the
    # two still render differently in Programs and Features.
    assert a.differs_only_in_fourth_field(MsiVersion.parse("1.0.0"))
    # A real version change is not this problem.
    assert not a.differs_only_in_fourth_field(MsiVersion.parse("1.0.1"))
    # And two identical versions are not either.
    assert not a.differs_only_in_fourth_field(MsiVersion.parse("1.0.0.4"))


def test_ordering_across_fields():
    ordered = [
        MsiVersion.parse("0.9.9"),
        MsiVersion.parse("1.0.0"),
        MsiVersion.parse("1.0.1"),
        MsiVersion.parse("1.1.0"),
        MsiVersion.parse("2.0.0"),
    ]
    shuffled = [ordered[3], ordered[0], ordered[4], ordered[2], ordered[1]]
    assert sorted(shuffled) == ordered
    assert max(shuffled) == MsiVersion.parse("2.0.0")
    assert min(shuffled) == MsiVersion.parse("0.9.9")


def test_comparison_with_a_foreign_type_is_not_implemented():
    assert MsiVersion(1).__eq__("1.0.0") is NotImplemented
    assert MsiVersion(1).__lt__("1.0.0") is NotImplemented


def _old_products_row(**kwargs):
    defaults = {
        "upgrade_code": UPGRADE_CODE,
        "action_property": "OLDPRODUCTS",
        "version_max": SHIPPED,
        "attributes": (
            UpgradeAttributes.MIGRATE_FEATURES | UpgradeAttributes.VERSION_MIN_INCLUSIVE
        ),
    }
    defaults.update(kwargs)
    return UpgradeRow(**defaults)


def _newer_product_row(**kwargs):
    defaults = {
        "upgrade_code": UPGRADE_CODE,
        "action_property": "AI_NEWERPRODUCTFOUND",
        "version_min": SHIPPED,
        "attributes": UpgradeAttributes.ONLY_DETECT,
    }
    defaults.update(kwargs)
    return UpgradeRow(**defaults)


def _installed(version: str, upgrade_code: str = UPGRADE_CODE, language: int | None = None):
    return InstalledProduct(
        product_code="{BA4D6ED3-C916-4F0E-BB5B-A197DA437380}",
        upgrade_code=upgrade_code,
        version=MsiVersion.parse(version),
        language=language,
    )


def test_upgrade_code_match_is_case_insensitive():
    row = _old_products_row()
    assert row.matches(UPGRADE_CODE.lower(), MsiVersion.parse("0.9.0"))


def test_a_different_family_never_matches():
    row = _old_products_row()
    assert not row.matches("{00000000-0000-0000-0000-000000000000}", MsiVersion.parse("0.9.0"))


def test_max_bound_is_exclusive_unless_the_flag_is_set():
    row = _old_products_row()
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.9"))
    assert not row.matches(UPGRADE_CODE, SHIPPED)


def test_max_bound_inclusive_flag():
    row = _old_products_row(
        attributes=UpgradeAttributes.VERSION_MAX_INCLUSIVE | UpgradeAttributes.VERSION_MIN_INCLUSIVE
    )
    assert row.matches(UPGRADE_CODE, SHIPPED)


def test_min_bound_is_exclusive_unless_the_flag_is_set():
    row = _newer_product_row()
    assert not row.matches(UPGRADE_CODE, SHIPPED)
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("1.0.1"))


def test_min_bound_inclusive_flag():
    row = _newer_product_row(
        attributes=UpgradeAttributes.ONLY_DETECT | UpgradeAttributes.VERSION_MIN_INCLUSIVE
    )
    assert row.matches(UPGRADE_CODE, SHIPPED)


def test_open_ended_row_matches_anything_in_the_family():
    row = UpgradeRow(upgrade_code=UPGRADE_CODE, action_property="ANY")
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("0.0.1"))
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("255.255.65535"))


def test_language_list_is_an_allow_list_by_default():
    row = _old_products_row(language="1033,1049")
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.0"), language=1049)
    assert not row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.0"), language=1036)


def test_language_list_inverts_when_exclusive():
    row = _old_products_row(
        language="1033,1049",
        attributes=UpgradeAttributes.LANGUAGES_EXCLUSIVE | UpgradeAttributes.VERSION_MIN_INCLUSIVE,
    )
    assert not row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.0"), language=1049)
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.0"), language=1036)


def test_unknown_installed_language_never_narrows_a_match():
    row = _old_products_row(language="1033")
    assert row.matches(UPGRADE_CODE, MsiVersion.parse("0.9.0"), language=None)


def test_older_product_is_scheduled_for_removal():
    decision = evaluate_upgrades(
        [_installed("0.9.4")],
        [_old_products_row(), _newer_product_row()],
    )
    assert [p.version for p in decision.removable] == [MsiVersion.parse("0.9.4")]
    assert decision.blocking == ()
    assert decision.properties == ("OLDPRODUCTS",)
    assert not decision.is_blocked


def test_newer_product_blocks_the_install():
    decision = evaluate_upgrades(
        [_installed("1.2.0")],
        [_old_products_row(), _newer_product_row()],
    )
    assert decision.removable == ()
    assert [p.version for p in decision.blocking] == [MsiVersion.parse("1.2.0")]
    assert decision.properties == ("AI_NEWERPRODUCTFOUND",)
    assert decision.is_blocked


def test_same_version_matches_neither_row():
    decision = evaluate_upgrades(
        [_installed("1.0.0")],
        [_old_products_row(), _newer_product_row()],
    )
    assert decision == evaluate_upgrades([], [])


def test_a_revision_only_bump_is_not_seen_as_newer():
    decision = evaluate_upgrades(
        [_installed("1.0.0.7")],
        [_old_products_row(), _newer_product_row()],
    )
    assert not decision.is_blocked
    assert decision.removable == ()


def test_detect_only_wins_when_both_rows_match():
    catch_all_remove = UpgradeRow(upgrade_code=UPGRADE_CODE, action_property="OLDPRODUCTS")
    catch_all_block = UpgradeRow(
        upgrade_code=UPGRADE_CODE,
        action_property="AI_NEWERPRODUCTFOUND",
        attributes=UpgradeAttributes.ONLY_DETECT,
    )
    decision = evaluate_upgrades([_installed("0.5.0")], [catch_all_remove, catch_all_block])
    assert decision.is_blocked
    assert decision.removable == ()
    assert decision.properties == ("OLDPRODUCTS", "AI_NEWERPRODUCTFOUND")


def test_products_from_other_families_are_ignored():
    decision = evaluate_upgrades(
        [_installed("0.9.0", upgrade_code="{11111111-1111-1111-1111-111111111111}")],
        [_old_products_row(), _newer_product_row()],
    )
    assert decision.removable == ()
    assert decision.blocking == ()
    assert decision.properties == ()


def test_several_old_products_are_all_collected():
    decision = evaluate_upgrades(
        [_installed("0.8.0"), _installed("0.9.0")],
        [_old_products_row()],
    )
    assert len(decision.removable) == 2
    assert decision.properties == ("OLDPRODUCTS",)


def test_attribute_helpers_read_the_bits():
    row = _newer_product_row()
    assert row.detect_only
    assert not row.min_inclusive
    assert not row.max_inclusive
