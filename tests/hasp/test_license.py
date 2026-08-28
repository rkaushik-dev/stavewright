from __future__ import annotations

from datetime import date

import pytest

from stavewright.errors import LicenseError
from stavewright.finding import Severity
from stavewright.hasp.license import (
    EXPIRY_WARNING_DAYS,
    FEATURE_NAMES,
    MODEL_NAMES,
    Feature,
    HaspKey,
    check_license,
    parse_key,
    required_features,
)
from stavewright.model.profile import TerminalModel

FULL = "HASP-0042/SJ6+TIJ/PRINT+VERIFY+MULTIHEAD/4/2026-12-31"
BASIC = "HASP-0007/MINI/PRINT/1/-"
UNLIMITED_KEY = "HASP-9999/*/PRINT+VERIFY+MULTIHEAD+GS1DM+DATABASE+REMOTE+OCR+AUDIT/*/-"
TODAY = date(2025, 6, 24)


def test_a_full_key_parses():
    key = parse_key(FULL)
    assert key.serial == "HASP-0042"
    assert key.models == frozenset({TerminalModel.SJET_600, TerminalModel.SJET_600_TIJ})
    assert key.has(Feature.PRINT)
    assert key.has(Feature.VERIFY)
    assert key.has(Feature.MULTI_HEAD)
    assert not key.has(Feature.OCR)
    assert key.max_heads == 4
    assert key.expires == date(2026, 12, 31)


def test_a_basic_key_parses():
    key = parse_key(BASIC)
    assert key.models == frozenset({TerminalModel.SJET_MINI})
    assert key.max_heads == 1
    assert key.expires is None


def test_an_unlimited_key_parses():
    key = parse_key(UNLIMITED_KEY)
    assert key.models == frozenset()
    assert key.max_heads is None
    assert len(key.feature_names) == len(FEATURE_NAMES)


@pytest.mark.parametrize("text", [FULL, BASIC, UNLIMITED_KEY])
def test_keys_round_trip(text):
    assert parse_key(text).format() == text


def test_leading_and_trailing_space_is_ignored():
    assert parse_key(f"  {FULL}  ").serial == "HASP-0042"


@pytest.mark.parametrize(
    "text",
    ["", "HASP-0042", "HASP-0042/SJ6/PRINT/4", "HASP-0042/SJ6/PRINT/4/2026-12-31/extra"],
)
def test_the_wrong_number_of_fields_is_refused(text):
    with pytest.raises(LicenseError, match="five fields"):
        parse_key(text)


def test_an_unknown_machine_is_refused():
    with pytest.raises(LicenseError, match="names a machine") as excinfo:
        parse_key("HASP-0042/SJ9/PRINT/4/-")
    assert excinfo.value.context["value"] == "SJ9"


def test_an_unknown_feature_is_refused():
    with pytest.raises(LicenseError, match="names a feature") as excinfo:
        parse_key("HASP-0042/SJ6/TELEPORT/4/-")
    assert excinfo.value.context["value"] == "TELEPORT"


def test_a_non_numeric_head_count_is_refused():
    with pytest.raises(LicenseError, match="head count is not a number"):
        parse_key("HASP-0042/SJ6/PRINT/four/-")


def test_a_bad_expiry_is_refused():
    with pytest.raises(LicenseError, match="not an ISO date"):
        parse_key("HASP-0042/SJ6/PRINT/4/31-12-2026")


def test_a_key_with_no_serial_is_refused():
    with pytest.raises(LicenseError, match="no serial"):
        HaspKey(serial="", models=frozenset(), features=Feature.PRINT)


def test_a_key_allowing_no_heads_is_refused():
    with pytest.raises(LicenseError, match="fewer than one head"):
        HaspKey(serial="X", models=frozenset(), features=Feature.PRINT, max_heads=0)


def test_a_key_with_no_features_parses():
    key = parse_key("HASP-0000/SJ6/-/1/-")
    assert key.features is Feature.NONE
    assert key.feature_names == ()


def test_covers_model():
    key = parse_key(FULL)
    assert key.covers_model(TerminalModel.SJET_600)
    assert key.covers_model(TerminalModel.SJET_600_TIJ)
    assert not key.covers_model(TerminalModel.SJET_MINI)


def test_an_unlimited_key_covers_every_model():
    key = parse_key(UNLIMITED_KEY)
    for model in MODEL_NAMES.values():
        assert key.covers_model(model)


def test_covers_heads():
    key = parse_key(FULL)
    assert key.covers_heads(1)
    assert key.covers_heads(4)
    assert not key.covers_heads(5)


def test_an_unlimited_key_covers_any_head_count():
    assert parse_key(UNLIMITED_KEY).covers_heads(64)


def test_expiry():
    key = parse_key(FULL)
    assert not key.is_expired(date(2026, 12, 30))
    assert not key.is_expired(date(2026, 12, 31))
    assert key.is_expired(date(2027, 1, 1))


def test_a_key_with_no_expiry_never_expires():
    assert not parse_key(BASIC).is_expired(date(2099, 1, 1))
    assert parse_key(BASIC).days_left(TODAY) is None


def test_days_left():
    key = parse_key(FULL)
    assert key.days_left(date(2026, 12, 1)) == 30
    assert key.days_left(date(2026, 12, 31)) == 0
    assert key.days_left(date(2027, 1, 1)) == -1


def test_describe_a_key_with_an_expiry():
    assert parse_key(FULL).describe() == "HASP-0042: 3 features, 4 heads, expires 2026-12-31"


def test_describe_a_key_without_one():
    assert parse_key(BASIC).describe() == "HASP-0007: 1 features, 1 heads, no expiry"


def test_describe_an_unlimited_key():
    assert "any heads" in parse_key(UNLIMITED_KEY).describe()


def test_a_single_head_terminal_needs_only_printing(profile):
    assert required_features(profile) is Feature.PRINT


def test_a_multi_head_terminal_needs_the_multi_head_feature(profile):
    two = profile.model_copy(update={"head_count": 2})
    assert required_features(two) == (Feature.PRINT | Feature.MULTI_HEAD)


def test_verification_is_asked_for_explicitly(profile):
    assert required_features(profile, verification=True) == (Feature.PRINT | Feature.VERIFY)


def test_a_good_key_produces_nothing(profile):
    assert check_license(parse_key(FULL), profile, today=TODAY) == []


def test_no_key_is_an_error(profile):
    findings = check_license(None, profile, today=TODAY)
    assert findings[0].severity is Severity.ERROR
    assert "no licence key" in findings[0].message


def test_the_wrong_key_serial_is_an_error(profile):
    licensed = profile.model_copy(update={"hasp_serial": "HASP-0099"})
    findings = check_license(parse_key(FULL), licensed, today=TODAY)
    assert findings[0].context["found"] == "HASP-0042"


def test_the_right_key_serial_passes(profile):
    licensed = profile.model_copy(update={"hasp_serial": "HASP-0042"})
    assert check_license(parse_key(FULL), licensed, today=TODAY) == []


def test_an_expired_key_is_an_error(profile):
    findings = check_license(parse_key(FULL), profile, today=date(2027, 6, 1))
    assert any("expired" in f.message for f in findings)
    assert all(f.severity is Severity.ERROR for f in findings if "expired" in f.message)


def test_a_key_close_to_expiry_is_a_warning(profile):
    soon = date(2026, 12, 31 - EXPIRY_WARNING_DAYS + 1)
    findings = check_license(parse_key(FULL), profile, today=soon)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["days_left"] <= EXPIRY_WARNING_DAYS


def test_a_key_comfortably_in_date_is_quiet(profile):
    assert check_license(parse_key(FULL), profile, today=date(2025, 1, 1)) == []


def test_a_key_that_does_not_cover_the_machine(profile):
    findings = check_license(parse_key(BASIC), profile, today=TODAY)
    machine = [f for f in findings if "does not cover" in f.message]
    assert machine[0].context["machine"] == "s-jet600"


def test_a_key_with_too_few_heads(profile):
    four_head = profile.model_copy(update={"head_count": 4})
    key = parse_key("HASP-0042/SJ6/PRINT+MULTIHEAD/2/-")
    findings = check_license(key, four_head, today=TODAY)
    heads = [f for f in findings if "fewer heads" in f.message]
    assert heads[0].context["heads"] == 4
    assert heads[0].context["licensed"] == 2


def test_a_four_head_terminal_behind_a_two_head_key_is_the_case_this_exists_for(profile):
    four_head = profile.model_copy(update={"head_count": 4})
    key = parse_key("HASP-0042/SJ6/PRINT+MULTIHEAD/2/-")
    assert any(f.is_error for f in check_license(key, four_head, today=TODAY))


def test_a_missing_feature_is_an_error(profile):
    two_head = profile.model_copy(update={"head_count": 2})
    key = parse_key("HASP-0042/SJ6/PRINT/4/-")
    findings = check_license(key, two_head, today=TODAY)
    missing = [f for f in findings if "missing a feature" in f.message]
    assert missing[0].context["missing"] == ["MULTIHEAD"]


def test_an_explicitly_requested_feature_is_checked(profile):
    key = parse_key("HASP-0042/SJ6/PRINT/4/-")
    findings = check_license(key, profile, needed=Feature.PRINT | Feature.OCR, today=TODAY)
    assert findings[0].context["missing"] == ["OCR"]


def test_several_problems_are_all_reported(profile):
    four_head = profile.model_copy(
        update={"head_count": 4, "hasp_serial": "HASP-0099"}
    )
    key = parse_key("HASP-0042/MINI/PRINT/2/2020-01-01")
    findings = check_license(key, four_head, today=TODAY)
    messages = " ".join(f.message for f in findings)
    assert "not the one this terminal is licensed" in messages
    assert "expired" in messages
    assert "does not cover" in messages
    assert "fewer heads" in messages
    assert "missing a feature" in messages


def test_feature_flags_combine():
    combined = Feature.PRINT | Feature.VERIFY
    key = HaspKey(serial="X", models=frozenset(), features=combined)
    assert key.has(Feature.PRINT)
    assert key.has(Feature.VERIFY)
    assert not key.has(Feature.OCR)


def test_feature_names_are_in_table_order():
    key = parse_key(UNLIMITED_KEY)
    assert key.feature_names == tuple(FEATURE_NAMES)
