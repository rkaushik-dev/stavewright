from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from stavewright.clock import frozen, utc
from stavewright.errors import VerifyError
from stavewright.verify.history import History, RunSummary
from stavewright.verify.retention import (
    DEFAULT_KEEP_DAYS,
    DEFAULT_KEEP_RUNS,
    Keep,
    RetentionPlan,
    apply_retention,
    plan_retention,
)

NOW = utc(2025, 6, 2, 12)


def run(
    name: str,
    terminal: str = "LINE-PC-04",
    days_ago: int = 1,
    *,
    finished: bool = True,
    damaged: str = "",
) -> RunSummary:
    started = NOW - timedelta(days=days_ago)
    return RunSummary(
        path=Path(f"run-{name}.jsonl"),
        run_id=name,
        terminal=terminal,
        started_at=started,
        finished_at=started + timedelta(minutes=20) if finished else None,
        outcome="complete" if finished else "",
        completed=12,
        damaged=damaged,
    )


def plan(*runs: RunSummary, **kwargs) -> RetentionPlan:
    return plan_retention(History(runs=list(runs)), moment=NOW, **kwargs)


def fate(result: RetentionPlan, name: str) -> Keep:
    return next(d.keep for d in result if d.path.name == f"run-{name}.jsonl")


def test_an_empty_history_gives_an_empty_plan():
    result = plan()
    assert len(result) == 0
    assert result.paths == []
    assert result.counts() == {}


def test_the_only_run_for_a_terminal_is_the_latest():
    assert fate(plan(run("a", days_ago=4000)), "a") is Keep.LATEST


def test_the_latest_is_kept_however_old_it_is():
    result = plan(run("old", days_ago=4000), run("older", days_ago=5000), keep_runs=0)
    assert fate(result, "old") is Keep.LATEST
    assert fate(result, "older") is Keep.EXPIRED


def test_a_run_that_never_finished_is_kept():
    result = plan(run("a"), run("stuck", days_ago=4000, finished=False), keep_runs=0)
    assert fate(result, "stuck") is Keep.UNFINISHED


def test_a_damaged_journal_is_kept():
    result = plan(run("a"), run("bad", days_ago=4000, damaged="line 3 is not JSON"), keep_runs=0)
    assert fate(result, "bad") is Keep.DAMAGED


def test_a_recent_run_is_kept_by_age():
    result = plan(run("a"), run("b", days_ago=10), keep_runs=1)
    assert fate(result, "b") is Keep.RECENT


def test_an_old_run_past_the_count_is_expired():
    result = plan(run("a"), run("b", days_ago=4000), keep_runs=1)
    assert fate(result, "b") is Keep.EXPIRED


def test_the_count_rule_keeps_what_the_age_rule_would_drop():
    result = plan(run("a"), run("b", days_ago=4000), keep_runs=5)
    assert fate(result, "b") is Keep.WITHIN_COUNT


def test_the_age_rule_keeps_what_the_count_rule_would_drop():
    result = plan(run("a"), run("b", days_ago=10), keep_runs=1)
    assert fate(result, "b") is Keep.RECENT


def test_the_two_rules_are_per_terminal():
    result = plan(
        run("a1", "LINE-PC-04"),
        run("a2", "LINE-PC-04", days_ago=4000),
        run("b1", "LINE-PC-05", days_ago=4000),
        keep_runs=1,
    )
    assert fate(result, "a2") is Keep.EXPIRED
    assert fate(result, "b1") is Keep.LATEST


def test_the_newest_run_per_terminal_is_the_one_that_is_kept():
    result = plan(
        run("older", days_ago=100),
        run("newer", days_ago=2),
        keep_runs=0,
    )
    assert fate(result, "newer") is Keep.LATEST
    assert fate(result, "older") is Keep.RECENT


def test_an_unfinished_run_does_not_count_as_the_latest():
    # The latest finished run is what says what is on the machine.
    result = plan(
        run("stuck", days_ago=1, finished=False),
        run("done", days_ago=100),
        keep_runs=0,
    )
    assert fate(result, "stuck") is Keep.UNFINISHED
    assert fate(result, "done") is Keep.LATEST


def test_the_plan_separates_kept_from_expired():
    result = plan(run("a"), run("b", days_ago=4000), keep_runs=1)
    assert [d.path.name for d in result.kept] == ["run-a.jsonl"]
    assert [d.path.name for d in result.expired] == ["run-b.jsonl"]


def test_the_paths_are_what_would_be_deleted():
    result = plan(run("a"), run("b", days_ago=4000), keep_runs=1)
    assert result.paths == [Path("run-b.jsonl")]


def test_counting_by_reason():
    result = plan(run("a"), run("b", days_ago=4000), run("c", days_ago=10), keep_runs=1)
    assert result.counts() == {"expired": 1, "latest": 1, "recent": 1}


def test_the_plan_describes_itself():
    lines = plan(run("a"), run("b", days_ago=4000), keep_runs=1).describe()
    assert lines == ["keep run-a.jsonl (latest)", "delete run-b.jsonl (expired)"]


def test_the_plan_is_in_a_stable_order():
    first = plan(run("b"), run("a"), keep_runs=9).describe()
    second = plan(run("a"), run("b"), keep_runs=9).describe()
    assert first == second


def test_only_expired_is_not_kept():
    assert not Keep.EXPIRED.is_kept
    assert all(keep.is_kept for keep in Keep if keep is not Keep.EXPIRED)


@pytest.mark.parametrize("kwargs", [{"keep_days": -1}, {"keep_runs": -1}])
def test_a_negative_limit_is_refused(kwargs):
    with pytest.raises(VerifyError, match="cannot be negative"):
        plan(run("a"), **kwargs)


def test_zero_limits_are_allowed_and_still_keep_the_latest():
    result = plan(run("a", days_ago=4000), run("b", days_ago=5000), keep_days=0, keep_runs=0)
    assert fate(result, "a") is Keep.LATEST
    assert fate(result, "b") is Keep.EXPIRED


def test_the_defaults_are_the_documented_ones():
    assert DEFAULT_KEEP_DAYS == 730
    assert DEFAULT_KEEP_RUNS == 10


def test_the_clock_is_used_when_no_moment_is_given():
    history = History(runs=[run("a"), run("b", days_ago=10)])
    with frozen(NOW + timedelta(days=4000)):
        result = plan_retention(history, keep_runs=1)
    assert any(decision.keep is Keep.EXPIRED for decision in result)


def _at(summary: RunSummary, path: Path) -> RunSummary:
    return replace(summary, path=path)


def test_applying_a_plan_is_a_dry_run_by_default(tmp_path):
    keep = tmp_path / "run-a.jsonl"
    drop = tmp_path / "run-b.jsonl"
    for path in (keep, drop):
        path.write_text("{}\n", encoding="utf-8")
    history = History(runs=[_at(run("a"), keep), _at(run("b", days_ago=4000), drop)])
    removed = apply_retention(plan_retention(history, moment=NOW, keep_runs=1))
    assert removed == [drop]
    assert drop.exists()


def test_applying_a_plan_for_real_removes_the_files(tmp_path):
    keep = tmp_path / "run-a.jsonl"
    drop = tmp_path / "run-b.jsonl"
    for path in (keep, drop):
        path.write_text("{}\n", encoding="utf-8")
    history = History(
        runs=[_at(run("a"), keep), _at(run("b", days_ago=4000), drop)]
    )
    result = plan_retention(history, moment=NOW, keep_runs=1)
    removed = apply_retention(result, dry_run=False)
    assert removed == [drop]
    assert not drop.exists()
    assert keep.exists()


def test_applying_a_plan_that_removes_nothing(tmp_path):
    assert apply_retention(plan(run("a")), dry_run=False) == []


def test_a_file_that_cannot_be_removed_stops_the_run(tmp_path):
    missing = tmp_path / "run-b.jsonl"
    history = History(runs=[_at(run("a"), tmp_path / "run-a.jsonl"), _at(run("b", days_ago=4000), missing)])
    result = plan_retention(history, moment=NOW, keep_runs=1)
    with pytest.raises(VerifyError, match="cannot remove a journal") as caught:
        apply_retention(result, dry_run=False)
    assert caught.value.context["removed"] == 0
