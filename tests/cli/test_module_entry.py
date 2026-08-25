"""Running the toolkit as a module, for real, in a subprocess.

Every other CLI test drives the Typer app object through the test runner, which
never asks whether the process starts. That gap hid a live defect for weeks:
``python -m v4t_setup.cli.main`` imported the module, built the command tree and
exited 0 without running a single command, and five CI steps were spelled that
way. They passed. They were checking nothing.

So these are deliberately subprocess tests. The thing being checked is that the
interpreter reaches ``run()``, and no in process test can check that. The rule
they encode: a command that produces no output and exits 0 is not a pass, and
any check whose answer can be "nothing happened" needs a positive control.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from v4t_setup.__about__ import __version__

MODULES = ["v4t_setup", "v4t_setup.cli.main"]


def module_run(module: str, *args: str, cwd=None) -> subprocess.CompletedProcess[str]:
    """Run one of the module entry points and hand back the finished process.

    The pipes are read as UTF-8 rather than as whatever the console codepage
    is. Help output is drawn with box characters, and on a Windows bench the
    default is cp1252, which cannot decode them and fails the read rather than
    the command.

    The child is run without coverage. pytest-cov hooks subprocesses through
    these variables and a .pth file, and each of these children then writes its
    own data file. Combining those with the parent's fails outright when the
    two disagree about branch coverage, which took out the whole coverage run
    and reported itself as an INTERNALERROR after the suite had passed. The
    numbers were wrong before that anyway: a process that starts, prints and
    exits inflates the CLI modules and says nothing about the tests.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_") and key != "COVERAGE_PROCESS_START"
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        cwd=cwd,
        timeout=120,
    )


@pytest.mark.parametrize("module", MODULES)
def test_the_module_entry_point_actually_runs(module):
    """The regression itself. Exit 0 with an empty stdout is the bug."""
    result = module_run(module, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"v4t-setup {__version__}"


@pytest.mark.parametrize("module", MODULES)
def test_the_module_entry_point_prints_help_with_no_arguments(module):
    """no_args_is_help prints the help and stops, and click calls that a pass.

    Typing the bare command is somebody asking what this is, not an error, so
    the zero here is deliberate and worth pinning.
    """
    result = module_run(module)
    assert "Commissioning toolkit" in result.stdout
    assert result.returncode == 0
    assert "bundle" in result.stdout


@pytest.mark.parametrize("module", MODULES)
def test_a_subcommand_group_reaches_its_commands(module):
    result = module_run(module, "bundle", "--help")
    assert result.returncode == 0, result.stderr
    for command in ("inspect", "msi", "match", "msi-diff", "msi-export"):
        assert command in result.stdout


def test_a_command_that_reports_findings_exits_one(tmp_path):
    """doctor in an empty directory has one warning and no settings file."""
    result = module_run("v4t_setup", "doctor", "--strict", cwd=tmp_path)
    assert result.returncode == 1, result.stdout
    assert "no settings file was found" in result.stdout


def test_the_same_command_without_strict_exits_zero(tmp_path):
    result = module_run("v4t_setup", "doctor", cwd=tmp_path)
    assert result.returncode == 0, result.stdout


def test_an_unknown_command_says_so_and_fails():
    result = module_run("v4t_setup", "polish")
    assert result.returncode != 0
    assert "polish" in result.stdout + result.stderr


def test_the_rules_listing_comes_out_of_the_code():
    """rules imports five rule sets, so it is a cheap import smoke test too."""
    result = module_run("v4t_setup", "rules")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert any(line.startswith("preflight: ") for line in lines)
    assert any(line.startswith("gs1 label: ") for line in lines)
    assert len(lines) > 20


def test_the_format_listing_matches_what_the_renderers_offer():
    from v4t_setup.report.render import RENDERERS

    result = module_run("v4t_setup", "formats")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == sorted(RENDERERS)


def test_nothing_is_written_to_stderr_on_a_clean_run():
    """The log goes to stderr, and at the default level a listing is silent.

    Worth pinning because a stray print or a default log level of DEBUG makes
    every piped invocation noisy, and nobody notices until a build log is
    unreadable.
    """
    result = module_run("v4t_setup", "formats")
    assert result.stderr == ""
