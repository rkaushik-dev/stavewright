"""The names the package exports, and the README that documents them.

A name leaving ``v4t_setup.__all__`` is a breaking change for anybody who
imported it, so the list is asserted rather than left to drift.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import v4t_setup

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

EXPORTED = {
    "MANUFACTURER",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "UPGRADE_CODE",
    "Architecture",
    "Executor",
    "Finding",
    "Manifest",
    "Plan",
    "Severity",
    "Stager",
    "Step",
    "StepKind",
    "TerminalModel",
    "TerminalProfile",
    "V4TError",
    "__version__",
    "build_manifest",
    "build_plan",
    "diff_plans",
    "dry_run",
    "normalise",
    "open_bundle",
    "read_aip",
    "read_manifest",
    "reconcile",
    "run_preflight",
}


def test_the_package_exports_the_flow():
    assert set(v4t_setup.__all__) == EXPORTED


def test_every_exported_name_is_importable():
    for name in v4t_setup.__all__:
        assert hasattr(v4t_setup, name), name


def test_the_export_list_is_ordered_the_way_every_other_one_is():
    # Constants, then classes, then functions, each group alphabetical. That is
    # what ruff's RUF022 enforces and what every __all__ in this package uses.
    # A plain sorted() would put Architecture above MANUFACTURER, which is not
    # the convention here.
    def group(name: str) -> int:
        if name.isupper():
            return 0
        if name[0].isupper():
            return 1
        return 2

    assert v4t_setup.__all__ == sorted(v4t_setup.__all__, key=lambda n: (group(n), n))


def test_no_name_is_exported_twice():
    assert len(v4t_setup.__all__) == len(set(v4t_setup.__all__))


def test_the_flow_in_the_docstring_actually_works(tmp_path):
    # The example in the package docstring, run for real against the shipped
    # project file. A docstring that has drifted from the API is worse than no
    # docstring, because somebody will copy it.
    from v4t_setup import build_manifest, build_plan, normalise, read_aip
    from v4t_setup.config.profiles import read_profile

    profile_file = tmp_path / "LINE-PC-04.toml"
    profile_file.write_text(
        'terminal_id = "LINE-PC-04"\n'
        'machine = "s-jet600"\n'
        'fpga_revision = "PCDC-1.4"\n',
        encoding="utf-8",
    )

    project = normalise(read_aip(REPO_ROOT / "setup.aip"))
    manifest = build_manifest(project, REPO_ROOT, digest_payload=False).manifest
    profile = read_profile(profile_file)
    plan = build_plan(manifest, profile, project.directories).plan
    assert len(plan) > 40


def test_importing_the_package_does_not_pull_in_the_windows_target():
    # The whole point of the target port is that the Windows surface is reached
    # deliberately. Importing the library should not drag it in.
    #
    # This asserts on our own module rather than on winreg, because on Windows
    # winreg is already in sys.modules by the time anything of ours runs: the
    # standard library pulls it in through mimetypes. Asserting on winreg looks
    # like a stronger test and is really a test of CPython's import graph.
    #
    # Done in a subprocess on purpose. The first version purged v4t_setup from
    # sys.modules and re-imported it in process, which left the rest of the
    # suite holding the old class objects. Five unrelated tests started failing
    # on isinstance and on dataclass equality, because a dataclass compares
    # other.__class__ is self.__class__ and there were suddenly two of every
    # class.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import v4t_setup; "
            "print('v4t_setup.target.windows' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "False"


def test_the_package_imports_on_its_own():
    result = subprocess.run(
        [sys.executable, "-c", "import v4t_setup; print(v4t_setup.__version__)"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == v4t_setup.__version__


def test_there_is_a_readme():
    assert README.is_file()
    assert README.read_text(encoding="utf-8").startswith("# V4T-Setup")


def test_the_readme_records_the_known_findings():
    # These findings are load bearing for several tests. If somebody takes them
    # out of the README they should have to notice.
    readme = README.read_text(encoding="utf-8")
    assert "00125682945601" in readme
    assert "Matricode" in readme
    assert "ERT" in readme


def test_the_readme_lists_every_command_group():
    from v4t_setup.cli.main import app

    readme = README.read_text(encoding="utf-8")
    for group in app.registered_groups:
        assert group.name in readme, group.name


def test_the_readme_exit_codes_match_the_cli():
    from v4t_setup.cli.common import EXIT_FINDINGS, EXIT_OK, EXIT_TOOLKIT

    readme = README.read_text(encoding="utf-8")
    for code in (EXIT_OK, EXIT_FINDINGS, EXIT_TOOLKIT):
        assert f"| {code} |" in readme


def test_the_readme_names_the_settings_file():
    from v4t_setup.config.settings import CONFIG_FILE_NAME

    assert CONFIG_FILE_NAME in README.read_text(encoding="utf-8")
