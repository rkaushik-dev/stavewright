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

from datetime import timedelta
from pathlib import Path

import typer

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
from v4t_setup.config.profiles import load_directory
from v4t_setup.fleet.roster import Roster, read_roster
from v4t_setup.fleet.schedule import DEFAULT_PER_MACHINE, read_windows, schedule_rollout
from v4t_setup.fleet.waves import check_rollout, plan_rollout
from v4t_setup.report.builders import rollout_report, roster_report, schedule_report

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _load(roster_path: Path, profiles: Path | None, *beside: Path | None) -> Roster:
    """Read the roster and the profiles it references.

    :param beside: other documents this command was given. They are usually in
        the same directory as the profiles, and the profile loader reads every
        TOML it finds, so each one has to be named or it gets parsed as a
        profile and fails on a file the operator put there deliberately. Every
        command that grows an argument has to add it here.
    """
    require_file(roster_path, "roster file")
    profile_dir = profiles or roster_path.parent
    require_directory(profile_dir, "profile directory")
    others = [path.name for path in beside if path is not None]
    known = load_directory(profile_dir, skip=[roster_path.name, *others])
    if not known:
        fail(f"no terminal profiles in {profile_dir}")
    return read_roster(roster_path, known)


@app.command("list")
@handle_errors
def list_terminals(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = typer.Option(
        None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
    ),
    line: str = typer.Option(None, "--line", "-l", help="Only this production line."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Show every terminal in a plant, least disruptive first."""
    get_context(ctx)
    roster = _load(roster_path, profiles)
    if line is not None:
        if line not in roster.lines:
            fail(f"no line called {line}, the roster has {', '.join(roster.lines)}")
        roster = Roster(plant=roster.plant, entries=tuple(roster.on_line(line)))
    raise typer.Exit(emit(roster_report(roster), fmt, output))


@app.command("plan")
@handle_errors
def plan(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = typer.Option(
        None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
    ),
    only: list[str] = typer.Option(
        None, "--only", help="Terminal to include. Repeat it. Order is ignored."
    ),
    pending: bool = typer.Option(
        False, "--pending", help="Only machines that have never been commissioned."
    ),
    batch: int = typer.Option(
        None, "--batch", "-b", help="Never put more than this many machines in one wave."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Work out the wave order for a plant."""
    get_context(ctx)
    roster = _load(roster_path, profiles)

    wanted = list(only or [])
    if pending:
        never = [entry.terminal_id for entry in roster.uncommissioned()]
        wanted = sorted(set(wanted) & set(never)) if wanted else never
        if not wanted:
            fail("every terminal in this roster has been commissioned already")

    rollout = plan_rollout(roster, only=wanted or None, batch_limit=batch)
    problems = check_rollout(roster, rollout, whole_plant=not wanted)
    raise typer.Exit(emit(rollout_report(rollout, problems), fmt, output))


@app.command("schedule")
@handle_errors
def schedule(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    calendar: Path = typer.Argument(..., help="The maintenance calendar."),
    profiles: Path = typer.Option(
        None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
    ),
    engineers: int = typer.Option(1, "--engineers", "-e", help="People on the job."),
    minutes: int = typer.Option(
        None, "--minutes", help="Minutes per machine. Defaults to the bench figure."
    ),
    pending: bool = typer.Option(
        False, "--pending", help="Only machines that have never been commissioned."
    ),
    batch: int = typer.Option(
        None, "--batch", "-b", help="Never put more than this many machines in one wave."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Put the waves into the windows the plant has offered.

    Exits with findings when a wave fits nowhere, which is the answer this
    command exists to give.
    """
    get_context(ctx)
    require_file(calendar, "maintenance calendar")
    roster = _load(roster_path, profiles, calendar)

    wanted: list[str] = []
    if pending:
        wanted = [entry.terminal_id for entry in roster.uncommissioned()]
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
    raise typer.Exit(emit(schedule_report(placed, windows), fmt, output))


@app.command("next")
@handle_errors
def next_wave(
    ctx: typer.Context,
    roster_path: Path = typer.Argument(..., help="The plant roster."),
    profiles: Path = typer.Option(
        None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
    ),
    batch: int = typer.Option(
        None, "--batch", "-b", help="Never put more than this many machines in one wave."
    ),
) -> None:
    """Print the terminals in the next wave, one per line.

    Deliberately plain. This is the one command whose output goes into a shell
    loop, so it prints terminal ids and nothing else.
    """
    get_context(ctx)
    roster = _load(roster_path, profiles)
    never = [entry.terminal_id for entry in roster.uncommissioned()]
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
    profiles: Path = typer.Option(
        None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
    ),
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
