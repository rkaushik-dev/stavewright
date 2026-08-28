from __future__ import annotations

from datetime import timedelta

import pytest

from stavewright.clock import frozen, utc
from stavewright.finding import Severity
from stavewright.model.manifest import Manifest
from stavewright.preflight.bench import (
    DEFAULT_BENCH_CHECKS,
    MAX_CLOCK_SKEW,
    MIN_FREE_BYTES,
    BenchContext,
    BundleSignedCheck,
    ClockCheck,
    JournalWritableCheck,
    LaptopSpaceCheck,
    OperatorCheck,
    TrustedKeysCheck,
    check_bench,
)
from stavewright.ruleset import duplicate_names, rule_names

NOW = utc(2025, 6, 3, 1, 30)


def context(tmp_path=None, **kwargs) -> BenchContext:
    base = {
        "manifest": Manifest(),
        "signature_present": False,
        "journal_root": tmp_path,
        "laptop_free_bytes": MIN_FREE_BYTES * 2,
        "operator": "R Hughes",
    }
    return BenchContext(**{**base, **kwargs})


def messages(findings) -> str:
    return " | ".join(finding.message for finding in findings)


def test_the_checks_have_unique_names():
    assert duplicate_names(DEFAULT_BENCH_CHECKS) == []


def test_every_check_is_namespaced():
    assert all(name.startswith("bench/") for name in rule_names(DEFAULT_BENCH_CHECKS))


def test_every_check_has_a_title():
    assert all(check.title for check in DEFAULT_BENCH_CHECKS)


def test_a_ready_bench_with_an_unsigned_bundle(tmp_path):
    findings = check_bench(context(tmp_path))
    assert [f.subject for f in findings] == ["signature", "clock"]
    assert {f.severity for f in findings} == {Severity.WARNING, Severity.INFO}


def test_a_ready_bench_with_a_signed_bundle_produces_only_the_clock_note(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "bench.pem").write_text("-----BEGIN PUBLIC KEY-----", encoding="utf-8")
    entry = context(tmp_path, signature_present=True, trusted_keys=keys)
    findings = check_bench(entry)
    assert [f.subject for f in findings] == ["clock"]
    assert findings[0].severity is Severity.INFO


def test_an_unsigned_bundle_is_a_warning(tmp_path):
    findings = BundleSignedCheck().check(context(tmp_path), None)
    assert findings[0].severity is Severity.WARNING
    assert "no signature" in findings[0].message


def test_a_signed_bundle_says_nothing(tmp_path):
    assert BundleSignedCheck().check(context(tmp_path, signature_present=True), None) == []


def test_no_manifest_means_no_opinion_about_signatures():
    assert BundleSignedCheck().check(BenchContext(), None) == []


def test_a_signed_bundle_with_no_keys_is_an_error(tmp_path):
    findings = TrustedKeysCheck().check(context(tmp_path, signature_present=True), None)
    assert findings[0].severity is Severity.ERROR
    assert "no keys to check it with" in findings[0].message


def test_a_signed_bundle_with_keys_passes(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "bench.pem").write_text("-----BEGIN PUBLIC KEY-----", encoding="utf-8")
    entry = context(tmp_path, signature_present=True, trusted_keys=keys)
    assert TrustedKeysCheck().check(entry, None) == []


def test_a_key_directory_with_no_keys_in_it(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    entry = context(tmp_path, signature_present=True, trusted_keys=keys)
    assert TrustedKeysCheck().check(entry, None) != []


def test_an_unsigned_bundle_needs_no_keys(tmp_path):
    assert TrustedKeysCheck().check(context(tmp_path), None) == []


def test_no_reference_clock_is_an_information_finding(tmp_path):
    findings = ClockCheck().check(context(tmp_path), None)
    assert findings[0].severity is Severity.INFO
    assert "nothing checked" in findings[0].message


def test_a_clock_that_agrees_passes(tmp_path):
    entry = context(tmp_path, laptop_clock=NOW, reference_clock=NOW + timedelta(minutes=1))
    assert ClockCheck().check(entry, None) == []


def test_a_clock_that_is_out_is_an_error(tmp_path):
    entry = context(tmp_path, laptop_clock=NOW, reference_clock=NOW + timedelta(hours=2))
    findings = ClockCheck().check(entry, None)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["skew_seconds"] == 7200


def test_the_skew_is_measured_either_way(tmp_path):
    behind = context(tmp_path, laptop_clock=NOW, reference_clock=NOW - timedelta(hours=2))
    assert ClockCheck().check(behind, None)[0].severity is Severity.ERROR


def test_a_skew_exactly_at_the_limit_passes(tmp_path):
    entry = context(tmp_path, laptop_clock=NOW, reference_clock=NOW + MAX_CLOCK_SKEW)
    assert ClockCheck().check(entry, None) == []


def test_the_clock_falls_back_to_the_toolkit_clock(tmp_path):
    entry = context(tmp_path, reference_clock=NOW)
    with frozen(NOW + timedelta(hours=3)):
        findings = ClockCheck().check(entry, None)
    assert findings[0].severity is Severity.ERROR


def test_plenty_of_room_passes(tmp_path):
    assert LaptopSpaceCheck().check(context(tmp_path), None) == []


def test_a_full_laptop_is_an_error(tmp_path):
    entry = context(tmp_path, laptop_free_bytes=1024)
    findings = LaptopSpaceCheck().check(entry, None)
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["wanted_bytes"] == MIN_FREE_BYTES


def test_unknown_free_space_says_nothing(tmp_path):
    assert LaptopSpaceCheck().check(context(tmp_path, laptop_free_bytes=None), None) == []


def test_a_journal_directory_that_exists_passes(tmp_path):
    assert JournalWritableCheck().check(context(tmp_path), None) == []


def test_no_journal_directory_configured_is_an_error():
    findings = JournalWritableCheck().check(context(None, journal_root=None), None)
    assert findings[0].severity is Severity.ERROR
    assert "nothing would be recorded" in findings[0].message


def test_a_journal_path_that_is_a_file_is_an_error(tmp_path):
    path = tmp_path / "journal"
    path.write_text("not a directory", encoding="utf-8")
    findings = JournalWritableCheck().check(context(tmp_path, journal_root=path), None)
    assert findings[0].severity is Severity.ERROR
    assert "rather than a directory" in findings[0].message


def test_a_journal_directory_that_does_not_exist_yet_is_only_a_note(tmp_path):
    entry = context(tmp_path, journal_root=tmp_path / "not-yet")
    findings = JournalWritableCheck().check(entry, None)
    assert findings[0].severity is Severity.INFO
    assert "will be created" in findings[0].message


def test_a_named_operator_passes(tmp_path):
    assert OperatorCheck().check(context(tmp_path), None) == []


def test_nobody_named_is_a_warning(tmp_path):
    findings = OperatorCheck().check(context(tmp_path, operator="   "), None)
    assert findings[0].severity is Severity.WARNING
    assert "the customer will ask" in findings[0].message


def test_a_bench_that_is_not_ready_at_all(tmp_path):
    entry = BenchContext(manifest=Manifest(), signature_present=True)
    findings = check_bench(entry)
    subjects = {finding.subject for finding in findings}
    assert subjects == {"trusted_keys", "clock", "journal_root", "operator"}


def test_the_checks_run_in_order(tmp_path):
    entry = BenchContext(manifest=Manifest(), signature_present=True)
    order = [finding.rule for finding in check_bench(entry)]
    assert order == sorted(order, key=lambda name: rule_names(DEFAULT_BENCH_CHECKS).index(name))


@pytest.mark.parametrize("name", rule_names(DEFAULT_BENCH_CHECKS))
def test_any_check_can_be_waived(name, tmp_path):
    entry = BenchContext(manifest=Manifest(), signature_present=True)
    findings = check_bench(entry, waive={name: "because"})
    assert all(finding.rule != name for finding in findings)


def test_the_limits_are_the_documented_ones():
    assert timedelta(minutes=5) == MAX_CLOCK_SKEW
    assert MIN_FREE_BYTES == 2 * 1024 * 1024 * 1024
