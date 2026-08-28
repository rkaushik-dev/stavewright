"""Fixtures for driving the command line.

Every CLI test runs in its own directory with its own settings file, because
the top level callback searches upwards for one and a test that picked up the
repository's own file would pass or fail depending on where it was run from.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest
from typer.testing import CliRunner

from stavewright.cli.main import app
from stavewright.config.settings import CONFIG_FILE_NAME
from stavewright.model.manifest import Manifest
from stavewright.model.payload import PayloadItem, PayloadKind

REPO_ROOT = Path(__file__).resolve().parents[2]
AIP = REPO_ROOT / "setup.aip"
GS1_DATA = REPO_ROOT / "Matricode" / "Quattro" / "Templates" / "GS1-1.txt"
TEMPLATE = REPO_ROOT / "Matricode" / "Quattro" / "Templates" / "T1.spf"

PROFILE_TOML = """
terminal_id = "LINE-PC-04"
machine = "s-jet600"
architecture = "x64"
head_count = 1
fpga_revision = "PCDC-1.4"
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def bench(tmp_path, monkeypatch) -> Path:
    """An empty commissioning directory with a settings file, cwd'd into."""
    (tmp_path / CONFIG_FILE_NAME).write_text(
        'staging_root = "_staging"\n'
        'journal_root = "_journal"\n'
        "require_signature = false\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def profile_file(bench) -> Path:
    path = bench / "LINE-PC-04.toml"
    path.write_text(PROFILE_TOML, encoding="utf-8")
    return path


@pytest.fixture
def payload_root(bench) -> Path:
    root = bench / "build"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (root / "payload" / "qu_imager.dll").write_bytes(b"MZimager")
    return root


@pytest.fixture
def manifest_file(bench, payload_root) -> Path:
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="dir.appdir",
                kind=PayloadKind.DIRECTORY,
                name="S-jet600",
                directory="APPDIR",
            ),
            PayloadItem(
                item_id="file.v4t",
                kind=PayloadKind.FILE,
                name="V4T.exe",
                directory="APPDIR",
                source=PurePosixPath("payload/V4T.exe"),
            ),
            PayloadItem(
                item_id="file.imager",
                kind=PayloadKind.FILE,
                name="qu_imager.dll",
                directory="APPDIR",
                source=PurePosixPath("payload/qu_imager.dll"),
            ),
        ]
    )
    path = bench / "manifest.json"
    manifest.write(path)
    return path


@pytest.fixture
def staged_bundle(runner, bench, manifest_file, payload_root) -> Path:
    result = runner.invoke(
        app,
        ["-q", "bundle", "stage", str(manifest_file), "--source", str(payload_root)],
    )
    assert result.exit_code == 0, result.output
    roots = list((bench / "_staging").iterdir())
    assert len(roots) == 1
    return roots[0]


def json_output(result) -> dict:
    return json.loads(result.stdout)
