"""``stavewright run`` — the commands that touch a terminal.

Plan, preflight, apply, verify. In that order, and each one refuses to do
anything surprising: ``plan`` never writes to a machine, ``preflight`` never
writes to a machine, and ``apply`` refuses to start when preflight has not
been run or has found something.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import typer

from stavewright.apply.executor import Executor, dry_run
from stavewright.apply.resume import check_resume, read_resume_state, remaining
from stavewright.cli import options
from stavewright.cli.common import (
    AppContext,
    echo_lines,
    emit,
    fail,
    finish,
    get_context,
    handle_errors,
    require_directory,
    require_file,
)
from stavewright.config.profiles import read_profile
from stavewright.finding import Finding
from stavewright.model.directory import DirectoryTable
from stavewright.model.manifest import read_manifest
from stavewright.model.profile import TerminalProfile
from stavewright.plan.diff import crossed_irreversible, diff_plans
from stavewright.plan.planner import build_plan
from stavewright.preflight.bench import BenchContext, check_bench
from stavewright.preflight.checks import PreflightContext
from stavewright.preflight.runner import run_preflight
from stavewright.report.builders.terminal import (
    apply_report,
    attestation_report,
    bench_report,
    drift_report,
    history_report,
    plan_diff_report,
    plan_report,
    preflight_report,
    retention_report,
)
from stavewright.stage.stager import SIGNATURE_NAME, open_bundle
from stavewright.target.base import TargetSystem
from stavewright.target.memory import MemoryTarget
from stavewright.target.windows import IS_WINDOWS, WindowsTarget
from stavewright.templates.pack import read_template_set
from stavewright.verify.attest import attest, check_attestation, read_attestation
from stavewright.verify.history import find_unfinished, read_history
from stavewright.verify.reconcile import reconcile
from stavewright.verify.retention import (
    DEFAULT_KEEP_DAYS,
    DEFAULT_KEEP_RUNS,
    apply_retention,
    plan_retention,
)
from stavewright.verify.snapshot import capture, read_snapshot

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _load_profile(path: Path, terminal: str | None) -> TerminalProfile:
    require_file(path, "profile")
    return read_profile(path, terminal)


def _load_directories(aip: Path | None) -> DirectoryTable:
    if aip is None:
        fail("no project file, pass --aip so directories can be resolved")
    require_file(aip, "project file")
    from stavewright.aip.normalize import normalise
    from stavewright.aip.reader import read_aip

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
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Work out what would be done to a terminal. Writes nothing."""
    get_context(ctx)
    require_file(manifest_path, "manifest")
    result = build_plan(
        read_manifest(manifest_path),
        _load_profile(profile_path, terminal),
        _load_directories(aip),
    )
    finish(plan_report(result), fmt, output, strict=strict)


@app.command("preflight")
@handle_errors
def preflight(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="The bundle manifest."),
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    templates: Path = options.TEMPLATES,
    offline: bool = options.OFFLINE,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
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
    finish(preflight_report(report, profile.terminal_id), fmt, output, strict=strict)


@app.command("apply")
@handle_errors
def apply(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    templates: Path = options.TEMPLATES,
    attestation: Path = typer.Option(
        None, "--attest", help="Write a commissioning attestation here when the run finishes."
    ),
    resume: Path = typer.Option(
        None, "--resume", help="A journal from a run that stopped. Picks it up."
    ),
    dry: bool = typer.Option(False, "--dry-run", help="Apply to a scratch machine instead."),
    skip_preflight: bool = typer.Option(
        False, "--skip-preflight", help="Do not run preflight first. Say why in the ticket."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Commission a terminal."""
    shared = get_context(ctx)
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip))

    resumed: set[str] = set()
    if resume is not None:
        require_file(resume, "journal to resume")
        state = read_resume_state(resume)
        check_resume(state, result.plan)
        resumed = set(state.completed)
        left = remaining(state, result.plan)
        typer.echo(
            f"resuming run {state.run_id}: {len(resumed)} steps done, {len(left)} to go",
            err=True,
        )

    target = _open_target(shared, dry)
    if not skip_preflight:
        # An unfinished journal blocks a run, and resuming is the answer to it.
        # Reporting the journal being resumed as the reason not to resume it is
        # the sort of thing that gets --skip-preflight typed at two in the
        # morning, so the one being picked up does not count.
        unfinished = _unfinished(shared, profile.terminal_id)
        # Resolved on both sides. The journal root is normally a relative path
        # in the settings file and --resume is normally typed absolute, and
        # comparing those two as strings never matches.
        if (
            resume is not None
            and unfinished
            and Path(unfinished).resolve() == resume.resolve()
        ):
            unfinished = None
        context = PreflightContext(
            plan=result.plan,
            profile=profile,
            facts=target.facts(),
            payload_bytes=staged.total_bytes(),
            unfinished_journal=unfinished,
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
        outcome = executor.run(result.plan, shared.run_id, start_after=resumed)

    if attestation is not None:
        # Reconciled straight away, because an attestation with nothing behind
        # it says "never reconciled" and that is what an auditor asks about
        # first. It costs one pass over the plan and answers the question.
        record = attest(result.plan, outcome, reconcile(result.plan, target, staged))
        record.write(attestation)
        typer.echo(f"attestation {record.digest()} written to {attestation}", err=True)
    finish(apply_report(outcome, result.plan), fmt, output, strict=strict)


@app.command("audit")
@handle_errors
def audit(
    ctx: typer.Context,
    attestation_path: Path = typer.Argument(..., help="An attestation written by apply."),
    manifest_path: Path = typer.Option(
        None, "--manifest", help="The bundle it should be for, to check the plan digest."
    ),
    profile_path: Path = typer.Option(None, "--profile", "-p", help="The terminal profile."),
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Read a commissioning attestation and say whether it stands up.

    With ``--manifest`` and ``--profile`` it also rebuilds the plan and checks
    that this attestation is for that one. Without them it checks everything
    the document can answer on its own.
    """
    get_context(ctx)
    require_file(attestation_path, "attestation")
    record = read_attestation(attestation_path)

    plan = None
    if manifest_path is not None:
        if profile_path is None:
            fail("--manifest needs --profile as well, a plan is built from both")
        require_file(manifest_path, "manifest")
        plan = build_plan(
            read_manifest(manifest_path),
            _load_profile(profile_path, terminal),
            _load_directories(aip),
            stamp=False,
        ).plan
    findings = check_attestation(record, plan)
    finish(attestation_report(record, findings), fmt, output, strict=strict)


@app.command("verify")
@handle_errors
def verify(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    offline: bool = options.OFFLINE,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Compare a terminal against the bundle it was commissioned from."""
    shared = get_context(ctx)
    require_directory(bundle, "staged bundle")
    profile = _load_profile(profile_path, terminal)
    staged = open_bundle(bundle)
    result = build_plan(staged.manifest, profile, _load_directories(aip))
    target = _open_target(shared, offline)
    finish(drift_report(reconcile(result.plan, target, staged)), fmt, output, strict=strict)


@app.command("capture")
@handle_errors
def capture_terminal(
    ctx: typer.Context,
    bundle: Path = typer.Argument(..., help="A staged bundle directory."),
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the snapshot."),
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
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
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
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
    finish(drift_report(reconcile(result.plan, snapshot, None)), fmt, output, strict=strict)


@app.command("diff")
@handle_errors
def diff(
    ctx: typer.Context,
    old: Path = typer.Argument(..., help="The manifest a terminal is running now."),
    new: Path = typer.Argument(..., help="The manifest it would move to."),
    profile_path: Path = options.PROFILE,
    terminal: str = options.TERMINAL,
    aip: Path = options.AIP,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
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
    finish(
        plan_diff_report(result, crossed_irreversible(before, after, result)),
        fmt,
        output,
        strict=strict,
    )


@app.command("history")
@handle_errors
def history(
    ctx: typer.Context,
    terminal: str = options.TERMINAL_FILTER,
    journals: Path = options.JOURNALS,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """What has been done to a terminal, out of the journals."""
    shared = get_context(ctx)
    root = journals or shared.settings.journal_root
    require_directory(root, "journal directory")
    found = read_history(root)
    finish(history_report(found, terminal), fmt, output, strict=strict)


@app.command("bench")
@handle_errors
def bench(
    ctx: typer.Context,
    bundle: Path = typer.Argument(None, help="A staged bundle, when there is one yet."),
    operator: str = typer.Option(None, "--operator", help="Who is doing the work."),
    reference: str = typer.Option(
        None, "--reference-time", help="An ISO time from something that is not this laptop."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Check the laptop and the visit before anybody drives anywhere.

    Nothing here touches a terminal. Run it in the office, where a missing
    signing key is a five minute problem rather than a wasted day.
    """
    shared = get_context(ctx)
    manifest = None
    signature_present = False
    if bundle is not None:
        require_directory(bundle, "staged bundle")
        staged = open_bundle(bundle)
        manifest = staged.manifest
        signature_present = (bundle / SIGNATURE_NAME).is_file()

    context = BenchContext(
        manifest=manifest,
        bundle_root=bundle,
        signature_present=signature_present,
        trusted_keys=shared.settings.trusted_keys,
        journal_root=shared.settings.journal_root,
        laptop_free_bytes=_free_bytes(shared.settings.staging_root),
        reference_clock=_reference_time(reference),
        operator=operator or shared.settings.operator,
    )
    finish(bench_report(check_bench(context)), fmt, output, strict=strict)


def _free_bytes(root: Path) -> int | None:
    """Free space where the bundles go, or None when nobody can tell.

    The staging root may not exist yet on a laptop that has never staged
    anything, so this walks up to the first directory that does.
    """
    current = root.resolve()
    for candidate in [current, *current.parents]:
        if candidate.is_dir():
            return shutil.disk_usage(candidate).free
    return None


def _reference_time(text: str | None) -> datetime | None:
    """Parse ``--reference-time``, or stop with a message.

    :raises typer.Exit: when it is not a time. Guessing at it would be worse
        than not checking the clock, because the check would then pass.
    """
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        fail(f"--reference-time is not an ISO 8601 time: {text}")
    if parsed.tzinfo is None:
        fail("--reference-time needs an offset, otherwise it means nothing")
    return parsed


@app.command("journals")
@handle_errors
def journals_command(
    ctx: typer.Context,
    journals: Path = options.JOURNALS,
    keep_days: int = typer.Option(
        DEFAULT_KEEP_DAYS, "--keep-days", help="How long an ordinary finished run is kept."
    ),
    keep_runs: int = typer.Option(
        DEFAULT_KEEP_RUNS, "--keep-runs", help="Runs per terminal kept whatever their age."
    ),
    remove: bool = typer.Option(
        False, "--remove", help="Actually delete them. Without this it only says what it would."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Say which journals could be cleared out, and clear them out on request.

    The default is to say and do nothing. These are the only record that a
    terminal was ever commissioned, so deleting one takes a flag.
    """
    shared = get_context(ctx)
    root = journals or shared.settings.journal_root
    require_directory(root, "journal directory")
    plan = plan_retention(read_history(root), keep_days=keep_days, keep_runs=keep_runs)
    if remove:
        removed = apply_retention(plan, dry_run=False)
        typer.echo(f"removed {len(removed)} journals from {root}", err=True)
    finish(retention_report(plan, applied=remove), fmt, output, strict=strict)


@app.command("profiles")
@handle_errors
def profiles(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A profile file or a directory of them."),
) -> None:
    """List the terminals a profile file or directory describes."""
    from stavewright.cli.common import echo_lines
    from stavewright.config.profiles import describe_profiles, load_directory, read_profiles

    get_context(ctx)
    if not path.exists():
        fail(f"profile path does not exist: {path}")
    found = load_directory(path) if path.is_dir() else read_profiles(path)
    echo_lines(describe_profiles(found))


__all__ = ["app"]
