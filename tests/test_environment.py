from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from stavewright.__about__ import __version__
from stavewright.environment import (
    MIN_TEMP_BYTES,
    MINIMUM_PYTHON,
    REQUIRED_PACKAGES,
    RULE,
    Environment,
    check_environment,
    collect,
)
from stavewright.finding import Severity


@pytest.fixture(scope="module")
def here() -> Environment:
    return collect()


def messages(findings) -> str:
    return " | ".join(finding.message for finding in findings)


def test_it_knows_which_toolkit_this_is(here):
    assert here.toolkit_version == __version__


def test_it_knows_which_python_this_is(here):
    import platform

    assert here.python_version == platform.python_version()


def test_it_names_the_interpreter(here):
    assert Path(here.python_executable).name.startswith("python")


def test_it_found_every_runtime_dependency(here):
    assert here.missing_packages == ()
    assert set(here.packages) == set(REQUIRED_PACKAGES)


def test_the_versions_look_like_versions(here):
    assert all(version[0].isdigit() for version in here.packages.values())


def test_it_measured_the_temporary_directory(here):
    assert here.temp_free_bytes is None or here.temp_free_bytes > 0


def test_no_settings_file_unless_one_is_given(here):
    assert here.config_file is None
    assert collect(config_file=Path("stavewright.toml")).config_file == Path("stavewright.toml")


def test_it_describes_itself(here):
    text = here.describe()
    assert text.startswith(f"stavewright {__version__}, Python ")


def test_the_facts_carry_a_line_per_package(here):
    facts = here.as_facts()
    for name in REQUIRED_PACKAGES:
        assert f"package.{name}" in facts


def test_the_facts_carry_the_settings_file(here):
    assert here.as_facts()["settings"] is None


def test_this_installation_has_no_errors(here):
    findings = check_environment(here)
    assert not any(finding.severity is Severity.ERROR for finding in findings)


def test_every_finding_carries_the_rule(here):
    assert all(finding.rule == RULE for finding in check_environment(here))


def test_an_old_python_is_an_error(here):
    old = replace(here, python_version="3.9.18")
    findings = check_environment(old)
    error = next(f for f in findings if f.subject == "python")
    assert error.severity is Severity.ERROR
    assert error.context["found"] == "3.9.18"
    assert error.context["wanted"] == ".".join(str(part) for part in MINIMUM_PYTHON)


def test_the_python_this_runs_on_is_new_enough(here):
    assert not any(f.subject == "python" for f in check_environment(here))


def test_a_missing_package_is_an_error(here):
    without = replace(here, missing_packages=("cryptography",))
    error = next(f for f in check_environment(without) if f.subject == "cryptography")
    assert error.severity is Severity.ERROR
    assert "not installed" in error.message


def test_several_missing_packages_each_get_a_finding(here):
    without = replace(here, missing_packages=("typer", "pydantic"))
    subjects = {f.subject for f in check_environment(without)}
    assert {"typer", "pydantic"} <= subjects


def test_being_off_windows_is_only_a_note(here):
    elsewhere = replace(here, is_windows=False, platform_name="Linux-6.1")
    finding = next(f for f in check_environment(elsewhere) if f.subject == "platform")
    assert finding.severity is Severity.INFO
    assert "will refuse" in finding.message


def test_being_on_windows_says_nothing_about_the_platform(here):
    on_windows = replace(here, is_windows=True)
    assert not any(f.subject == "platform" for f in check_environment(on_windows))


def test_a_full_temporary_directory_is_a_warning(here):
    tight = replace(here, temp_free_bytes=1024)
    finding = next(f for f in check_environment(tight) if f.subject == "temp")
    assert finding.severity is Severity.WARNING
    assert finding.context["wanted_bytes"] == MIN_TEMP_BYTES


def test_an_unmeasurable_temporary_directory_says_nothing(here):
    unknown = replace(here, temp_free_bytes=None)
    assert not any(f.subject == "temp" for f in check_environment(unknown))


def test_no_settings_file_is_a_warning(here):
    finding = next(f for f in check_environment(here) if f.subject == "settings")
    assert finding.severity is Severity.WARNING
    assert "needs its flags typed out" in finding.message


def test_a_settings_file_says_nothing(here):
    configured = replace(here, config_file=Path("stavewright.toml"))
    assert not any(f.subject == "settings" for f in check_environment(configured))


def test_a_thoroughly_broken_installation(here):
    broken = replace(
        here,
        python_version="3.8.0",
        missing_packages=("typer",),
        temp_free_bytes=0,
        config_file=None,
    )
    subjects = {f.subject for f in check_environment(broken)}
    assert {"python", "typer", "temp", "settings"} <= subjects
