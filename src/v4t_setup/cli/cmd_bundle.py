"""``v4t-setup bundle`` — building, signing and staging what goes on a terminal.

This is the office half of the job. Everything here happens where there is a
network and a person who can fix a payload, which is the point: by the time a
laptop walks onto a factory floor the bundle should already be known good.
"""

from __future__ import annotations

from pathlib import Path

import typer

from v4t_setup.aip.builder import build_manifest
from v4t_setup.aip.normalize import normalise
from v4t_setup.aip.reader import read_aip
from v4t_setup.cli.common import (
    DEFAULT_FORMAT,
    echo_lines,
    emit,
    fail,
    get_context,
    handle_errors,
    require_directory,
    require_file,
)
from v4t_setup.model.manifest import Manifest, read_manifest, utc_now
from v4t_setup.msi.cfb import read_compound_file
from v4t_setup.msi.compare import compare_project, compare_tables
from v4t_setup.msi.lint import lint_msi
from v4t_setup.msi.summary import read_summary
from v4t_setup.msi.tables import read_database, read_msi
from v4t_setup.report.builders.installer import msi_compare_report, msi_report
from v4t_setup.report.builders.terminal import stage_report
from v4t_setup.report.document import Report, table_section
from v4t_setup.signing import (
    KeyRing,
    generate_key,
    key_id_for,
    load_private_key,
    private_key_to_pem,
    public_key_to_pem,
    read_signature,
    sign_manifest,
    signature_path_for,
)
from v4t_setup.stage.stager import Stager
from v4t_setup.util.fsops import atomic_write_bytes

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("inspect")
@handle_errors
def inspect(
    ctx: typer.Context,
    aip: Path = typer.Argument(..., help="An Advanced Installer project file."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
) -> None:
    """Read a project file and say what is in it."""
    get_context(ctx)
    require_file(aip, "project file")
    project = normalise(read_aip(aip))
    report = Report(
        title="Advanced Installer project",
        subtitle=f"{project.manufacturer} {project.product_name} {project.product_version}",
        facts={
            "upgrade_code": project.upgrade_code,
            "all_users": "yes" if project.all_users else "no",
        },
    )
    report.add(
        table_section(
            "Tables",
            ["table", "rows"],
            [
                ["directories", str(len(project.directories))],
                ["components", str(len(project.components))],
                ["features", str(len(project.features))],
                ["files", str(len(project.files))],
                ["registry", str(len(project.registry))],
                ["upgrades", str(len(project.upgrades))],
            ],
        )
    )
    orphans = project.features.orphan_components(project.components)
    if orphans:
        report.add(
            table_section(
                "Components no feature installs",
                ["component"],
                [[name] for name in orphans],
            )
        )
    raise typer.Exit(emit(report, fmt))


@app.command("msi")
@handle_errors
def msi(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A built .msi."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Read a built installer, and check it makes sense on its own terms.

    An MSI is a relational database with nothing enforcing its foreign keys, so
    a component can name a directory that is not there and the build succeeds.
    Exits 1 when something is wrong with it.
    """
    get_context(ctx)
    require_file(path, "installer")
    source = read_compound_file(path)
    database = read_database(source, path)
    summary = read_summary(source)
    report = msi_report(database, summary, lint_msi(database, summary))
    raise typer.Exit(emit(report, fmt, output))


@app.command("match")
@handle_errors
def match(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A built .msi."),
    aip: Path = typer.Argument(..., help="The project file it should have come from."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Say whether an installer was built from a project file.

    Exits 1 when it was not, so a release script can gate on it. Comparison is
    by identity and table membership: two builds of the same project differ in
    every byte and are still the same product.
    """
    get_context(ctx)
    require_file(path, "installer")
    require_file(aip, "project file")
    database = read_msi(path)
    project = normalise(read_aip(aip))
    report = msi_compare_report(
        database,
        compare_tables(project, database),
        compare_project(project, database),
    )
    raise typer.Exit(emit(report, fmt, output))


@app.command("sign")
@handle_errors
def sign(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The manifest to sign."),
    key: Path = typer.Option(..., "--key", "-k", help="A PEM signing key."),
    password: str = typer.Option(None, "--password", help="Key password, if it has one."),
) -> None:
    """Sign a manifest, writing a detached signature beside it."""
    get_context(ctx)
    require_file(manifest_path, "manifest")
    require_file(key, "signing key")
    manifest = read_manifest(manifest_path)
    private = load_private_key(key.read_bytes(), password.encode() if password else None)
    signature = sign_manifest(manifest, private)
    destination = signature_path_for(manifest_path)
    signature.write(destination)
    typer.echo(f"signed {manifest.describe()}")
    typer.echo(f"key {signature.key_id}, signature at {destination}")


@app.command("verify")
@handle_errors
def verify(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The manifest to verify."),
    keys: Path = typer.Option(
        None, "--keys", help="A directory of trusted PEM public keys."
    ),
) -> None:
    """Check a manifest against its detached signature."""
    shared = get_context(ctx)
    require_file(manifest_path, "manifest")
    key_dir = keys or shared.settings.trusted_keys
    if key_dir is None:
        fail("no trusted key directory, pass --keys or set trusted_keys")
    require_directory(key_dir, "trusted key directory")

    ring = KeyRing()
    for pem in sorted(key_dir.glob("*.pem")):
        ring.add_pem(pem.read_bytes())
    if not len(ring):
        fail(f"no PEM public keys in {key_dir}")

    manifest = read_manifest(manifest_path)
    signature = read_signature(signature_path_for(manifest_path))
    ring.verify(manifest, signature)
    typer.echo(f"{manifest.describe()} verifies against key {signature.key_id}")


@app.command("keygen")
@handle_errors
def keygen(
    ctx: typer.Context,
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the private key."),
    password: str = typer.Option(None, "--password", help="Encrypt the key with this."),
) -> None:
    """Make a signing key and write both halves."""
    get_context(ctx)
    if out.exists():
        fail(f"refusing to overwrite an existing key: {out}")
    private = generate_key()
    atomic_write_bytes(out, private_key_to_pem(private, password.encode() if password else None))
    public_path = out.with_suffix(".pub.pem")
    atomic_write_bytes(public_path, public_key_to_pem(private.public_key()))
    typer.echo(f"key id {key_id_for(private.public_key())}")
    typer.echo(f"private key {out}")
    typer.echo(f"public key {public_path}")


@app.command("stage")
@handle_errors
def stage(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The manifest to stage."),
    source: Path = typer.Option(None, "--source", "-s", help="Where the payload is."),
    staging: Path = typer.Option(None, "--staging", help="Where to stage it."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Gather a bundle's payload onto this machine."""
    shared = get_context(ctx)
    require_file(manifest_path, "manifest")
    source_root = source or shared.settings.source_root
    require_directory(source_root, "payload source")
    manifest = read_manifest(manifest_path)

    signature = None
    signature_path = signature_path_for(manifest_path)
    if signature_path.is_file():
        signature = read_signature(signature_path)
    elif shared.settings.require_signature:
        fail(f"no signature beside {manifest_path} and signatures are required")

    stager = Stager(staging or shared.settings.staging_root)
    result = stager.stage(manifest, source_root, signature=signature)
    raise typer.Exit(emit(stage_report(result), fmt, output))


@app.command("build")
@handle_errors
def build(
    ctx: typer.Context,
    aip: Path = typer.Argument(..., help="An Advanced Installer project file."),
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the manifest."),
    source: Path = typer.Option(
        None, "--source", "-s", help="What the AIP source paths resolve against."
    ),
    install_level: int = typer.Option(
        None, "--install-level", help="Only include components selected at this level."
    ),
    no_digest: bool = typer.Option(
        False, "--no-digest", help="Skip reading the payload. Faster, and unsigned in effect."
    ),
    allow_missing: bool = typer.Option(
        False, "--allow-missing", help="Write the manifest even when payload is absent."
    ),
    notes: str = typer.Option("", "--notes", help="Anything worth recording."),
) -> None:
    """Generate a manifest from a project file."""
    shared = get_context(ctx)
    require_file(aip, "project file")
    source_root = source or aip.parent
    require_directory(source_root, "payload source")
    if out.exists():
        fail(f"refusing to overwrite an existing manifest: {out}")

    project = normalise(read_aip(aip))
    report = build_manifest(
        project,
        source_root,
        install_level=install_level or shared.settings.install_level,
        digest_payload=not no_digest,
        notes=notes,
    )
    # A manifest with holes in it is worse than no manifest, because it looks
    # finished. Writing one takes a flag and the flag is in the output.
    if not report.is_complete and not allow_missing:
        echo_lines(report.lines(), err=True)
        fail(
            f"{len(report.missing_sources)} payload sources are missing, "
            "pass --allow-missing to write the manifest anyway"
        )
    report.manifest.write(out)
    echo_lines(report.lines())
    typer.echo(f"wrote {out}")


@app.command("new")
@handle_errors
def new(
    ctx: typer.Context,
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the manifest."),
    notes: str = typer.Option("", "--notes", help="Anything worth recording."),
) -> None:
    """Write an empty manifest to start a bundle from."""
    get_context(ctx)
    if out.exists():
        fail(f"refusing to overwrite an existing manifest: {out}")
    manifest = Manifest(generated_at=utc_now(), notes=notes)
    manifest.write(out)
    typer.echo(f"wrote {out}")
    typer.echo(manifest.describe())


__all__ = ["app"]
