"""``v4t-setup run`` — the commands that touch a terminal.

Plan, preflight, apply, verify. In that order, and each one refuses to do
anything surprising: ``plan`` never writes to a machine, ``preflight`` never
writes to a machine, and ``apply`` refuses to start when preflight has not
been run or has found something.
"""

from __future__ import annotations

from pathlib import Path

import typer

from v4t_setup.apply.executor import Executor, dry_run
from v4t_setup.cli.common import (
    DEFAULT_FORMAT,
    AppContext,
    echo_lines,
    emit,
    fail,
    get_context,
    handle_errors,
    require_directory,
    require_file,
)
from v4t_setup.config.profiles import read_profile
from v4t_setup.finding import Finding
from v4t_setup.model.directory import DirectoryTable
from v4t_setup.model.manifest import read_manifest
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.diff import crossed_irreversible, diff_plans
from v4t_setup.plan.planner import build_plan
from v4t_setup.preflight.checks import PreflightContext
from v4t_setup.preflight.runner import run_preflight
from v4t_setup.report.builders import (
    apply_report,
    drift_report,
    history_report,
    plan_diff_report,
    plan_report,
    preflight_report,
)
from v4t_setup.stage.stager import open_bundle
from v4t_setup.target.base import TargetSystem
from v4t_setup.target.memory import MemoryTarget
from v4t_setup.target.windows import IS_WINDOWS, WindowsTarget
from v4t_setup.templates.pack import read_template_set
from v4t_setup.verify.history import find_unfinished, read_history
from v4t_setup.verify.reconcile import reconcile
from v4t_setup.verify.snapshot import capture, read_snapshot

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _load_profile(path: Path, terminal: str | None) -> TerminalProfile:
    require_file(path, "profile")
    return read_profile(path, terminal)


def _load_directories(aip: Path | None) -> DirectoryTable:
    if aip is None:
        fail("no project file, pass --aip so directories can be resolved")
    require_file(aip, "project file")
    from v4t_setup.aip.normalize import normalise
    from v4t_setup.aip.reader import read_aip

    return normalise(read_aip(aip)).directories


def _open_target(shared: AppContext, offline: bool) -> TargetSystem:
    if offline or not IS_WINDOWS:
        return MemoryTarget()
    return WindowsTarget()


def _required_fonts(directory: Path | None) -> frozenset[str]:
    """The fonts a template set names, for the preflight font check.

    That check has been in the runner from the start and has never had
    anything to check, because nothing collected the fonts. This is the
    collector: a directory of templates, read the same way ``template
    lint-set`` reads it.

    Nothing here lints. A template with a font the terminal does not have is a
    preflight finding about the terminal, and a template that is broken in some
    other way is a question for the quality group, not for the bench at two in
    the morning.
    """
    if directory is None:
        return frozenset()
    require_directory(directory, "template directory")
    return read_template_set(directory).fonts


def _one_line(finding: Finding) -> str:
    """A finding as one line, context and all.

    The message on its own is not enough here. "a font the templates use is
    neither installed nor being installed" without the font name is a warning
    the operator can do nothing with at two in the morning.
    """
    detail = ", ".join(f"{key}={finding.context[key]}" for key in sorted(finding.context))
    subject = f" [{finding.subject}]" if finding.subject else ""
    return f"{finding.message}{subject}" + (f" ({detail})" if detail else "")


def _unfinished(shared: AppContext, terminal: str) -> str | None:
    """The journal of a run against *terminal* that never finished, if any.

    A dry run against a scratch machine still reads the real journal directory,
    because the point of the check is whether somebody left a real terminal
    half commissioned.
    """
    run = find_unfinished(shared.settings.journal_root, terminal)
    return str(run.path) if run is not None else None


@app.command("plan")
@handle_errors
def plan(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The bundle manifest."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Work out what would be done to a terminal. Writes nothing."""
    get_context(ctx)
    require_file(manifest_path, "manifest")
    result = build_plan(
        read_manifest(manifest_path),
        _load_profile(profile_path, terminal),
        _load_directories(aip),
    )
    raise typer.Exit(emit(plan_report(result), fmt, output))


@app.command("preflight")
@handle_errors
def preflight(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The bundle manifest."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    templates: Path = typer.Option(
        None, "--templates", help="The template set, so its fonts are checked too."
    ),
    offline: bool = typer.Option(False, "--offline", help="Check against an empty machine."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Check a terminal is ready. Writes nothing."""
    shared = get_context(ctx)
    require_file(manifest_path, "manifest")
    profile = _load_profile(profile_path, terminal)
    result = build_plan(read_manifest(manifest_path), profile, _load_directories(aip))
    target = _open_target(shared, offline)
    context = PreflightContext(
        plan=result.plan,
        profile=profile,
        facts=target.facts(),
        unfinished_journal=_unfinished(shared, profile.terminal_id),
        required_fonts=_required_fonts(templates),
    )
    report = run_preflight(context, target, waive=shared.settings.waived_checks)
    raise typer.Exit(emit(preflight_report(report, profile.terminal_id), fmt, output))


@app.command("apply")
@handle_errors
def apply(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    templates: Path = typer.Option(
        None, "--templates", help="The template set, so its fonts are checked too."
    ),
    dry: bool = typer.Option(False, "--dry-run", help="Apply to a scratch machine instead."),
    skip_preflight: bool = typer.Option(
        False, "--skip-preflight", help="Do not run preflight first. Say why in the ticket."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Commission a terminal."""
    shared = get_context(ctx)
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip))

    target = _open_target(shared, dry)
    if not skip_preflight:
        context = PreflightContext(
            plan=result.plan,
            profile=profile,
            facts=target.facts(),
            payload_bytes=staged.total_bytes(),
            unfinished_journal=_unfinished(shared, profile.terminal_id),
            required_fonts=_required_fonts(templates),
        )
        checks = run_preflight(context, target, waive=shared.settings.waived_checks)
        if not checks.passed:
            emit(preflight_report(checks, profile.terminal_id), fmt, output)
            fail("preflight found problems, nothing was applied", code=1)
        # Preflight passed, so the run goes ahead, but warnings still have to
        # reach the operator. They go to stderr as lines rather than as a second
        # report, because stdout carries one document and --format json means
        # one document.
        echo_lines([f"preflight warning: {_one_line(f)}" for f in checks.findings], err=True)

    journal = shared.settings.journal_for(shared.run_id)
    if dry:
        outcome = dry_run(result.plan, target, staged, journal)
    else:
        executor = Executor(
            target,
            staged,
            journal,
            rollback_on_failure=shared.settings.rollback_on_failure,
        )
        outcome = executor.run(result.plan, shared.run_id)
    raise typer.Exit(emit(apply_report(outcome, result.plan), fmt, output))


@app.command("verify")
@handle_errors
def verify(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    offline: bool = typer.Option(False, "--offline", help="Check against an empty machine."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Compare a terminal against the bundle it was commissioned from."""
    shared = get_context(ctx)
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip))
    target = _open_target(shared, offline)
    raise typer.Exit(emit(drift_report(reconcile(result.plan, target, staged)), fmt, output))


@app.command("capture")
@handle_errors
def capture_terminal(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the snapshot."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
) -> None:
    """Read this terminal's state into a file somebody else can look at."""
    shared = get_context(ctx)
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip), stamp=False)
    snapshot = capture(result.plan, _open_target(shared, offline=False))
    snapshot.write(out)
    typer.echo(snapshot.describe())
    typer.echo(f"wrote {out}")


@app.command("offline")
@handle_errors
def offline_verify(
    ctx: typer.Context,
    snapshot_path: Path = typer.Argument(..., help="A snapshot taken on a terminal."),
    bundle: Path = typer.Argument(..., help="The staged bundle it should match."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Reconcile a snapshot against a bundle, away from the terminal."""
    get_context(ctx)
    require_file(snapshot_path, "snapshot")
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip), stamp=False)
    snapshot = read_snapshot(snapshot_path)
    if snapshot.plan_digest and snapshot.plan_digest != str(result.plan.digest()):
        # Not fatal. A snapshot taken against an older bundle is still the best
        # information anybody has about that machine, and saying so is more use
        # than refusing to look.
        typer.echo(
            "warning: this snapshot was taken against a different plan "
            f"({snapshot.plan_digest})",
            err=True,
        )
    raise typer.Exit(emit(drift_report(reconcile(result.plan, snapshot, None)), fmt, output))


@app.command("diff")
@handle_errors
def diff(
    ctx: typer.Context,
    old: Path = typer.Argument(..., help="The manifest a terminal is running now."),
    new: Path = typer.Argument(..., help="The manifest it would move to."),
    profile_path: Path = typer.Option(..., "--profile", "-p", help="The terminal profile."),
    terminal: str = typer.Option(None, "--terminal", "-t", help="Which terminal in the file."),
    aip: Path = typer.Option(None, "--aip", help="The project file, for the directory table."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Say what moving a terminal from one bundle to another would do."""
    get_context(ctx)
    require_file(old, "old manifest")
    require_file(new, "new manifest")
    profile = _load_profile(profile_path, terminal)
    directories = _load_directories(aip)
    before = build_plan(read_manifest(old), profile, directories, stamp=False).plan
    after = build_plan(read_manifest(new), profile, directories, stamp=False).plan
    result = diff_plans(before, after)
    raise typer.Exit(
        emit(plan_diff_report(result, crossed_irreversible(before, after, result)), fmt, output)
    )


@app.command("history")
@handle_errors
def history(
    ctx: typer.Context,
    terminal: str = typer.Option(None, "--terminal", "-t", help="Only this terminal."),
    journals: Path = typer.Option(None, "--journals", help="Where the journals are."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """What has been done to a terminal, out of the journals."""
    shared = get_context(ctx)
    root = journals or shared.settings.journal_root
    require_directory(root, "journal directory")
    found = read_history(root)
    raise typer.Exit(emit(history_report(found, terminal), fmt, output))


@app.command("profiles")
@handle_errors
def profiles(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A profile file or a directory of them."),
) -> None:
    """List the terminals a profile file or directory describes."""
    from v4t_setup.cli.common import echo_lines
    from v4t_setup.config.profiles import describe_profiles, load_directory, read_profiles

    get_context(ctx)
    if not path.exists():
        fail(f"profile path does not exist: {path}")
    found = load_directory(path) if path.is_dir() else read_profiles(path)
    echo_lines(describe_profiles(found))


__all__ = ["app"]
