from __future__ import annotations

import pytest
from typer.testing import CliRunner

from v4t_setup.cli.common import EXIT_FINDINGS, EXIT_OK, EXIT_TOOLKIT
from v4t_setup.cli.main import app

from .conftest import json_output

PROFILES = """
machine = "s-jet600"
architecture = "x64"
fpga_revision = "PCDC-1.4"

[terminals.EXPORT-01]
head_count = 2

[terminals.EXPORT-02]
head_count = 2

[terminals.SAMPLE-01]

[terminals.BENCH-01]
"""

ROSTER = """
plant = "Wrexham"

[line.export]
criticality = "critical"
shift_pattern = "continuous"

[line.export.terminal.EXPORT-01]
position = 1
commissioned = 2024-11-03

[line.export.terminal.EXPORT-02]
position = 2

[line.sample]

[line.sample.terminal.SAMPLE-01]
position = 1

[line.bench]
criticality = "spare"

[line.bench.terminal.BENCH-01]
position = 1
"""


@pytest.fixture
def plant(bench):
    (bench / "profiles.toml").write_text(PROFILES, encoding="utf-8")
    path = bench / "roster.toml"
    path.write_text(ROSTER, encoding="utf-8")
    return path


def run(runner: CliRunner, *args: str):
    return runner.invoke(app, ["-q", *args])


def test_the_group_is_in_the_help(runner):
    result = runner.invoke(app, ["--help"])
    assert "fleet" in result.output


def test_listing_a_plant(runner, plant):
    result = run(runner, "fleet", "list", str(plant))
    assert result.exit_code == EXIT_OK, result.output
    assert "Plant roster" in result.output
    assert "EXPORT-01" in result.output


def test_listing_is_in_working_order(runner, plant):
    result = run(runner, "fleet", "list", str(plant), "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    assert [row[0] for row in rows] == ["BENCH-01", "SAMPLE-01", "EXPORT-01", "EXPORT-02"]


def test_listing_one_line(runner, plant):
    result = run(runner, "fleet", "list", str(plant), "--line", "export", "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    assert {row[0] for row in rows} == {"EXPORT-01", "EXPORT-02"}


def test_a_line_that_is_not_there_lists_the_ones_that_are(runner, plant):
    result = run(runner, "fleet", "list", str(plant), "--line", "packing")
    assert result.exit_code == EXIT_TOOLKIT
    assert "bench, export, sample" in result.output


def test_a_missing_roster_is_a_toolkit_error(runner, bench):
    result = run(runner, "fleet", "list", str(bench / "gone.toml"))
    assert result.exit_code == EXIT_TOOLKIT
    assert "roster file does not exist" in result.output


def test_a_directory_with_no_profiles_says_so(runner, bench, plant, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run(runner, "fleet", "list", str(plant), "--profiles", str(empty))
    assert result.exit_code == EXIT_TOOLKIT
    assert "no terminal profiles" in result.output


def test_a_roster_naming_a_terminal_with_no_profile_carries_its_code(runner, bench, plant):
    (bench / "profiles.toml").write_text(
        PROFILES.replace("[terminals.SAMPLE-01]", "[terminals.SAMPLE-99]"), encoding="utf-8"
    )
    result = run(runner, "fleet", "list", str(plant))
    assert result.exit_code == EXIT_TOOLKIT
    assert "terminal with no profile" in result.output
    assert "terminal: SAMPLE-01" in result.output


def test_planning_a_plant(runner, plant):
    result = run(runner, "fleet", "plan", str(plant))
    assert result.exit_code == EXIT_OK, result.output
    assert "Rollout plan" in result.output


def test_the_plan_puts_the_spare_first(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    assert rows[0][2] == "BENCH-01"


def test_the_plan_counts_the_stoppages(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--format", "json")
    assert json_output(result)["facts"]["stoppages"] == "2"


def test_planning_a_subset(runner, plant):
    result = run(
        runner, "fleet", "plan", str(plant), "--only", "EXPORT-02", "--only", "BENCH-01",
        "--format", "json",
    )
    rows = json_output(result)["sections"][0]["rows"]
    assert [row[2] for row in rows] == ["BENCH-01", "EXPORT-02"]


def test_a_subset_plan_does_not_complain_about_what_it_left_out(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--only", "BENCH-01")
    assert result.exit_code == EXIT_OK, result.output
    assert "in no wave" not in result.output


def test_planning_only_what_has_never_been_done(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--pending", "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    covered = {name for row in rows for name in row[2].split(", ")}
    assert covered == {"BENCH-01", "SAMPLE-01", "EXPORT-02"}


def test_pending_and_only_intersect(runner, plant):
    result = run(
        runner, "fleet", "plan", str(plant), "--pending", "--only", "EXPORT-01",
        "--only", "BENCH-01", "--format", "json",
    )
    rows = json_output(result)["sections"][0]["rows"]
    assert [row[2] for row in rows] == ["BENCH-01"]


def test_a_fully_commissioned_plant_has_nothing_pending(runner, bench, plant):
    done = ROSTER.replace("position = 2", "position = 2\ncommissioned = 2025-01-04")
    done = done.replace(
        "[line.sample.terminal.SAMPLE-01]\nposition = 1",
        "[line.sample.terminal.SAMPLE-01]\nposition = 1\ncommissioned = 2025-01-05",
    )
    done = done.replace(
        "[line.bench.terminal.BENCH-01]\nposition = 1",
        "[line.bench.terminal.BENCH-01]\nposition = 1\ncommissioned = 2025-01-06",
    )
    plant.write_text(done, encoding="utf-8")
    result = run(runner, "fleet", "plan", str(plant), "--pending")
    assert result.exit_code == EXIT_TOOLKIT
    assert "commissioned already" in result.output


def test_a_batch_limit_narrows_the_waves(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--batch", "1", "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    assert all(", " not in row[2] for row in rows)


def test_a_batch_limit_of_zero_is_refused(runner, plant):
    result = run(runner, "fleet", "plan", str(plant), "--batch", "0")
    assert result.exit_code == EXIT_TOOLKIT
    assert "at least one machine" in result.output


def test_writing_the_plan_to_a_file(runner, bench, plant):
    out = bench / "plan.json"
    result = run(runner, "fleet", "plan", str(plant), "--format", "json", "--output", str(out))
    assert result.exit_code == EXIT_OK
    assert out.is_file()
    assert "Rollout plan" in out.read_text(encoding="utf-8")


def test_the_next_wave_prints_terminal_ids_and_nothing_else(runner, plant):
    result = run(runner, "fleet", "next", str(plant))
    assert result.exit_code == EXIT_OK, result.output
    assert result.output.split() == ["BENCH-01"]


def test_the_next_wave_respects_a_batch_limit(runner, plant):
    result = run(runner, "fleet", "next", str(plant), "--batch", "1")
    assert len(result.output.split()) == 1


def test_the_next_wave_of_a_finished_plant(runner, bench, plant):
    finished = ROSTER
    for name in ("EXPORT-02", "SAMPLE-01", "BENCH-01"):
        finished = finished.replace(
            f"[line.{name.split('-')[0].lower()}.terminal.{name}]\nposition",
            f"[line.{name.split('-')[0].lower()}.terminal.{name}]\ncommissioned = 2025-03-01\n"
            "position",
        )
    plant.write_text(finished, encoding="utf-8")
    result = run(runner, "fleet", "next", str(plant))
    assert result.exit_code == EXIT_TOOLKIT
    assert "commissioned already" in result.output


def test_what_feeds_a_terminal(runner, plant):
    result = run(runner, "fleet", "upstream", str(plant), "EXPORT-02")
    assert result.exit_code == EXIT_OK, result.output
    assert "EXPORT-01" in result.output


def test_the_first_machine_on_a_line_has_nothing_upstream(runner, plant):
    result = run(runner, "fleet", "upstream", str(plant), "EXPORT-01")
    assert result.exit_code == EXIT_OK
    assert "EXPORT-01 is first on export" in result.output


def test_asking_about_a_terminal_that_is_not_there(runner, plant):
    result = run(runner, "fleet", "upstream", str(plant), "GONE-01")
    assert result.exit_code == EXIT_TOOLKIT
    assert "no such terminal" in result.output


def test_profiles_can_live_somewhere_else(runner, bench, plant, tmp_path):
    elsewhere = tmp_path / "profiles"
    elsewhere.mkdir()
    (elsewhere / "plant.toml").write_text(PROFILES, encoding="utf-8")
    (bench / "profiles.toml").unlink()
    result = run(runner, "fleet", "list", str(plant), "--profiles", str(elsewhere))
    assert result.exit_code == EXIT_OK, result.output


def test_a_plan_that_does_not_hold_exits_with_findings(runner, bench, plant, monkeypatch):
    from v4t_setup.cli import cmd_fleet

    monkeypatch.setattr(cmd_fleet, "check_rollout", lambda *a, **k: ["something is wrong"])
    result = run(runner, "fleet", "plan", str(plant))
    assert result.exit_code == EXIT_FINDINGS
    assert "something is wrong" in result.output


CALENDAR = """
[[window]]
label = "monday night"
start = 2025-06-02T22:00:00Z
end = 2025-06-03T02:00:00Z

[[window]]
label = "tuesday night"
start = 2025-06-03T22:00:00Z
end = 2025-06-04T02:00:00Z
"""


@pytest.fixture
def calendar(bench):
    path = bench / "calendar.toml"
    path.write_text(CALENDAR, encoding="utf-8")
    return path


def test_scheduling_a_plant(runner, plant, calendar):
    result = run(runner, "fleet", "schedule", str(plant), str(calendar))
    assert result.exit_code == EXIT_OK, result.output
    assert "Rollout schedule" in result.output
    assert "monday night" in result.output


def test_the_schedule_books_every_wave(runner, plant, calendar):
    result = run(runner, "fleet", "schedule", str(plant), str(calendar), "--format", "json")
    facts = json_output(result)["facts"]
    assert facts["unplaced"] == "0"
    assert facts["booked"] == "4"


def test_the_first_booking_opens_the_window(runner, plant, calendar):
    result = run(runner, "fleet", "schedule", str(plant), str(calendar), "--format", "json")
    sections = json_output(result)["sections"]
    bookings = next(part for part in sections if part["heading"] == "Bookings")
    assert bookings["rows"][0][1] == "2025-06-02 22:00:00+00:00"


def test_more_engineers_change_nothing_when_every_wave_is_one_machine(runner, plant, calendar):
    # Which is this plant: two critical machines on one line, and the other
    # two alone on theirs. A second pair of hands has nothing to do.
    one = run(runner, "fleet", "schedule", str(plant), str(calendar), "--format", "json")
    three = run(
        runner, "fleet", "schedule", str(plant), str(calendar), "-e", "3", "--format", "json"
    )
    assert json_output(three)["facts"]["finishes"] == json_output(one)["facts"]["finishes"]


def test_more_engineers_finish_sooner_when_a_wave_has_several(runner, bench, plant, calendar):
    wide = "\n".join(
        ["plant = \"Wrexham\""]
        + [
            f"\n[line.line{n}]\n\n[line.line{n}.terminal.{name}]\nposition = 1"
            for n, name in enumerate(("EXPORT-01", "EXPORT-02", "SAMPLE-01"), start=1)
        ]
    )
    plant.write_text(wide, encoding="utf-8")
    one = run(runner, "fleet", "schedule", str(plant), str(calendar), "--format", "json")
    three = run(
        runner, "fleet", "schedule", str(plant), str(calendar), "-e", "3", "--format", "json"
    )
    assert json_output(three)["facts"]["finishes"] < json_output(one)["facts"]["finishes"]


def test_a_slower_bench_can_run_out_of_window(runner, plant, calendar):
    result = run(runner, "fleet", "schedule", str(plant), str(calendar), "--minutes", "600")
    assert result.exit_code == EXIT_FINDINGS
    assert "does not fit any offered window" in result.output


def test_scheduling_only_what_is_pending(runner, plant, calendar):
    result = run(
        runner, "fleet", "schedule", str(plant), str(calendar), "--pending", "--format", "json"
    )
    assert json_output(result)["facts"]["booked"] == "3"


def test_a_batch_limit_makes_more_waves(runner, plant, calendar):
    result = run(
        runner, "fleet", "schedule", str(plant), str(calendar), "--batch", "1", "--format", "json"
    )
    assert json_output(result)["facts"]["booked"] == "4"


def test_a_missing_calendar_is_a_toolkit_error(runner, bench, plant):
    result = run(runner, "fleet", "schedule", str(plant), str(bench / "gone.toml"))
    assert result.exit_code == EXIT_TOOLKIT
    assert "maintenance calendar does not exist" in result.output


def test_a_calendar_with_overlapping_windows_is_refused(runner, bench, plant, calendar):
    calendar.write_text(
        CALENDAR.replace("start = 2025-06-03T22:00:00Z", "start = 2025-06-03T01:00:00Z"),
        encoding="utf-8",
    )
    result = run(runner, "fleet", "schedule", str(plant), str(calendar))
    assert result.exit_code == EXIT_TOOLKIT
    assert "windows overlap" in result.output


def test_a_calendar_with_a_naive_time_is_refused(runner, bench, plant, calendar):
    calendar.write_text(
        CALENDAR.replace("2025-06-02T22:00:00Z", "2025-06-02T22:00:00"), encoding="utf-8"
    )
    result = run(runner, "fleet", "schedule", str(plant), str(calendar))
    assert result.exit_code == EXIT_TOOLKIT
    assert "needs an offset" in result.output


def test_an_empty_calendar_places_nothing(runner, bench, plant, calendar):
    calendar.write_text("", encoding="utf-8")
    result = run(runner, "fleet", "schedule", str(plant), str(calendar), "--format", "json")
    assert json_output(result)["facts"]["booked"] == "0"


def test_writing_the_schedule_to_a_file(runner, bench, plant, calendar):
    out = bench / "schedule.json"
    result = run(
        runner, "fleet", "schedule", str(plant), str(calendar),
        "--format", "json", "--output", str(out),
    )
    assert result.exit_code == EXIT_OK
    assert "Rollout schedule" in out.read_text(encoding="utf-8")
