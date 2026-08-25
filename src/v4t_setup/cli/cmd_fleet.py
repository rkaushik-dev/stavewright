"""``v4t-setup fleet`` — working out what order to commission a plant in.

Nothing here touches a terminal. It reads the roster and the profiles beside it
and answers the two questions the planning meeting actually asks: what have we
got, and what are we doing first.

The roster and the profile directory are separate arguments because they are
maintained by different people. Commissioning owns the profiles. The plant owns
the roster, and it changes when a line is re-laid rather than when a bundle
does.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import typer

from v4t_setup.cli import options
from v4t_setup.cli.common import (
    AppContext,
    echo_lines,
    fail,
    finish,
    get_context,
    handle_errors,
    require_directory,
    require_file,
)
from v4t_setup.config.profiles import load_directory
from v4t_setup.fleet.audit import audit_roster
from v4t_setup.fleet.roster import Roster, read_roster
from v4t_setup.fleet.schedule import DEFAULT_PER_MACHINE, read_windows, schedule_rollout
from v4t_setup.fleet.status import plant_status
from v4t_setup.fleet.sweep import find_snapshots, sweep
from v4t_setup.fleet.waves import check_rollout, plan_rollout
from v4t_setup.model.manifest import read_manifest
from v4t_setup.report.builders.fleet import (
    rollout_report,
    roster_audit_report,
    roster_report,
    schedule_report,
    status_report,
    sweep_report,
)
from v4t_setup.verify.history import History, read_history

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _load(roster_path: Path, profiles: Path | None) -> Roster:
    """Read the roster and the profiles it references.

    The roster and the calendar sit in the same directory as the profiles.
    Keeping them out of the scan is the loader's job, not this one's, because
    it was this one's for two commits and both commands that grew an argument
    forgot to update it.
    """
    require_file(roster_path, "roster file")
    profile_dir = profiles or roster_path.parent
    require_directory(profile_dir, "profile directory")
    known = load_directory(profile_dir)
    if not known:
        fail(f"no terminal profiles in {profile_dir}")
    return read_roster(roster_path, known)


@app.command("list")
@handle_errors
def list_terminals(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = options.PROFILE_DIR,
    line: str = typer.Option(None, "--line", "-l", help="Only this production line."),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Show every terminal in a plant, least disruptive first."""
    get_context(ctx)
    roster = _load(roster_path, profiles)
    if line is not None:
        if line not in roster.lines:
            fail(f"no line called {line}, the roster has {', '.join(roster.lines)}")
        roster = Roster(plant=roster.plant, entries=tuple(roster.on_line(line)))
    finish(roster_report(roster), fmt, output, strict=strict)


def _pending(shared: AppContext, roster: Roster, journals: Path | None) -> list[str]:
    """Terminals the bench still has work on.

    The roster's ``commissioned`` date is somebody's typing and the journals
    are the toolkit's own record. When there are journals to read, they decide,
    which means a machine somebody commissioned and forgot to write down does
    not get done twice.
    """
    root = journals or shared.settings.journal_root
    if root is None or not root.is_dir():
        return [entry.terminal_id for entry in roster.uncommissioned()]
    return plant_status(roster, read_history(root)).outstanding


@app.command("audit")
@handle_errors
def audit(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = options.PROFILE_DIR,
    today: str = typer.Option(
        None, "--today", help="Measure commissioning dates against this date."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Check the roster against the profiles beside it.

    Reading a roster only proves it is a roster. This asks the questions that
    need a second document: a terminal on two lines, a gap in the positions, a
    profile no line claims, a line mixing FPGA revisions.

    Run it after a line is re-laid and before the planning meeting. Everything
    it reports is cheap to fix on a Tuesday and expensive to find on a Sunday
    with the line down.
    """
    get_context(ctx)
    roster = _load(roster_path, profiles)
    known = load_directory(profiles or roster_path.parent)
    findings = audit_roster(roster, known, today=_today(today))
    finish(roster_audit_report(roster, findings), fmt, output, strict=strict)


def _today(text: str | None) -> date:
    """The date to measure against, from a flag or from the clock.

    A flag because the stale check is the only thing in this toolkit whose
    answer changes on its own overnight, and somebody reproducing a report from
    last month needs it to come out the same.
    """
    if text is None:
        return date.today()
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"--today is not a date: {text}")


@app.command("status")
@handle_errors
def status(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = options.PROFILE_DIR,
    journals: Path = options.JOURNALS,
    stale_days: int = typer.Option(
        365, "--stale-days", help="How old a finished run may be and still count."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Read the roster against the journals and say where the plant stands.

    Exits 1 when a terminal is part commissioned, because that one has to be
    dealt with before anything else moves. The two records disagreeing about
    whether a machine was ever done is a warning: it is a bookkeeping problem,
    not a machine somebody has to stand at.
    """
    shared = get_context(ctx)
    roster = _load(roster_path, profiles)
    root = journals or shared.settings.journal_root
    history = read_history(root) if root is not None and root.is_dir() else History()
    standing = plant_status(roster, history, stale_after=timedelta(days=stale_days))
    finish(status_report(standing), fmt, output, strict=strict)


@app.command("sweep")
@handle_errors
def sweep_command(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    snapshots: Path = typer.Argument(..., help="A directory of snapshots off the terminals."),
    manifest_path: Path = typer.Option(
        ..., "--manifest", "-m", help="The bundle they should all match."
    ),
    profiles: Path = options.PROFILE_DIR,
    aip: Path = options.AIP,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Reconcile a whole plant from snapshots, without visiting any of it.

    Exits 1 when a terminal has drifted. A terminal nobody has a snapshot of
    is a warning, because a sweep says nothing about a machine it never saw.
    """
    get_context(ctx)
    require_file(manifest_path, "manifest")
    require_directory(snapshots, "snapshot directory")
    roster = _load(roster_path, profiles)

    from v4t_setup.aip.normalize import normalise
    from v4t_setup.aip.reader import read_aip

    if aip is None:
        fail("no project file, pass --aip so directories can be resolved")
    require_file(aip, "project file")
    result = sweep(
        roster,
        read_manifest(manifest_path),
        normalise(read_aip(aip)).directories,
        find_snapshots(snapshots),
    )
    finish(sweep_report(result), fmt, output, strict=strict)


@app.command("plan")
@handle_errors
def plan(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = options.PROFILE_DIR,
    only: list[str] = typer.Option(
        None, "--only", help="Terminal to include. Repeat it. Order is ignored."
    ),
    pending: bool = options.PENDING,
    journals: Path = options.JOURNALS,
    batch: int = options.BATCH,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Work out the wave order for a plant."""
    shared = get_context(ctx)
    roster = _load(roster_path, profiles)

    wanted = list(only or [])
    if pending:
        never = _pending(shared, roster, journals)
        wanted = sorted(set(wanted) & set(never)) if wanted else never
        if not wanted:
            fail("every terminal in this roster has been commissioned already")

    rollout = plan_rollout(roster, only=wanted or None, batch_limit=batch)
    problems = check_rollout(roster, rollout, whole_plant=not wanted)
    finish(rollout_report(rollout, problems), fmt, output, strict=strict)


@app.command("schedule")
@handle_errors
def schedule(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    calendar: Path = typer.Argument(..., help="The maintenance calendar."),
    profiles: Path = options.PROFILE_DIR,
    engineers: int = typer.Option(1, "--engineers", "-e", help="People on the job."),
    minutes: int = typer.Option(
        None, "--minutes", help="Minutes per machine. Defaults to the bench figure."
    ),
    pending: bool = options.PENDING,
    journals: Path = options.JOURNALS,
    batch: int = options.BATCH,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Put the waves into the windows the plant has offered.

    Exits with findings when a wave fits nowhere, which is the answer this
    command exists to give.
    """
    shared = get_context(ctx)
    require_file(calendar, "maintenance calendar")
    roster = _load(roster_path, profiles)

    wanted: list[str] = []
    if pending:
        wanted = _pending(shared, roster, journals)
        if not wanted:
            fail("every terminal in this roster has been commissioned already")

    rollout = plan_rollout(roster, only=wanted or None, batch_limit=batch)
    windows = read_windows(calendar)
    placed = schedule_rollout(
        rollout,
        windows,
        engineers=engineers,
        per_machine=timedelta(minutes=minutes) if minutes else DEFAULT_PER_MACHINE,
    )
    finish(schedule_report(placed, windows), fmt, output, strict=strict)


@app.command("next")
@handle_errors
def next_wave(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = options.PROFILE_DIR,
    journals: Path = options.JOURNALS,
    batch: int = options.BATCH,
) -> None:
    """Print the terminals in the next wave, one per line.

    Deliberately plain. This is the one command whose output goes into a shell
    loop, so it prints terminal ids and nothing else.
    """
    shared = get_context(ctx)
    roster = _load(roster_path, profiles)
    never = _pending(shared, roster, journals)
    if not never:
        fail("every terminal in this roster has been commissioned already")
    rollout = plan_rollout(roster, only=never, batch_limit=batch)
    echo_lines(rollout.waves[0].terminal_ids)


@app.command("upstream")
@handle_errors
def upstream(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    terminal: str = typer.Argument(..., help="Which terminal."),
    profiles: Path = options.PROFILE_DIR,
) -> None:
    """Print what feeds a terminal, nearest first."""
    get_context(ctx)
    roster = _load(roster_path, profiles)
    feeding = roster.upstream_of(terminal)
    if not feeding:
        typer.echo(f"{terminal} is first on {roster.get(terminal).line}")
        return
    echo_lines([entry.describe() for entry in feeding])


__all__ = ["app"]
