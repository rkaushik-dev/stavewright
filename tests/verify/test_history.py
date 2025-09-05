from __future__ import annotations

import json

import pytest

from v4t_setup import clock
from v4t_setup.apply.journal import Journal, RecordKind
from v4t_setup.errors import VerifyError
from v4t_setup.verify.history import (
    History,
    RunSummary,
    find_unfinished,
    read_history,
    summarise_journal,
)

DAY_ONE = clock.utc(2026, 2, 3, 9, 0)
DAY_TWO = clock.utc(2026, 2, 4, 14, 30)
DAY_THREE = clock.utc(2026, 3, 11, 8, 15)


def write_run(
    root,
    run_id: str,
    terminal: str,
    *,
    at,
    outcome: str | None = "complete",
    fail_at: str | None = None,
    rollback: bool = False,
):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"run-{run_id}.jsonl"
    with clock.frozen(at), Journal(path) as journal:
        journal.begin("sha256:abc", terminal, run_id)
        for name in ("dir.appdir", "file.v4t", "reg.version"):
            journal.append(RecordKind.STEP_START, step_id=name)
            if name == fail_at:
                journal.append(RecordKind.STEP_FAILED, step_id=name, detail="boom")
                break
            journal.append(RecordKind.STEP_OK, step_id=name, changed=True)
        if rollback:
            journal.append(RecordKind.ROLLBACK_OK, step_id="file.v4t")
        if outcome is not None:
            journal.end(outcome)
    return path


@pytest.fixture
def journals(tmp_path):
    root = tmp_path / "_journal"
    write_run(root, "0001aaaa", "LINE-PC-04", at=DAY_ONE)
    write_run(root, "0002bbbb", "LINE-PC-05", at=DAY_TWO, fail_at="file.v4t",
              outcome="rolled-back", rollback=True)
    write_run(root, "0003cccc", "LINE-PC-04", at=DAY_THREE, outcome=None)
    return root


def test_a_clean_run_summarises(tmp_path):
    path = write_run(tmp_path / "j", "0001aaaa", "LINE-PC-04", at=DAY_ONE)
    run = summarise_journal(path)
    assert run.run_id == "0001aaaa"
    assert run.terminal == "LINE-PC-04"
    assert run.plan_digest == "sha256:abc"
    assert run.outcome == "complete"
    assert run.completed == 3
    assert run.is_complete
    assert run.is_readable
    assert not run.is_unfinished


def test_timestamps_come_off_the_records(tmp_path):
    path = write_run(tmp_path / "j", "0001aaaa", "LINE-PC-04", at=DAY_ONE)
    run = summarise_journal(path)
    assert run.started_at == DAY_ONE
    assert run.finished_at == DAY_ONE
    assert run.duration_seconds == 0


def test_a_run_with_no_end_record_has_no_finish_time(tmp_path):
    path = write_run(tmp_path / "j", "0001aaaa", "LINE-PC-04", at=DAY_ONE, outcome=None)
    run = summarise_journal(path)
    assert run.finished_at is None
    assert run.duration_seconds is None
    assert run.is_unfinished
    assert not run.is_complete


def test_a_failed_run_summarises(tmp_path):
    path = write_run(
        tmp_path / "j", "0002bbbb", "LINE-PC-05", at=DAY_TWO,
        fail_at="file.v4t", outcome="rolled-back", rollback=True,
    )
    run = summarise_journal(path)
    assert run.failed_step == "file.v4t"
    assert run.rolled_back == 1
    assert run.outcome == "rolled-back"
    assert not run.is_complete


def test_a_damaged_journal_is_kept_and_marked(tmp_path):
    path = write_run(tmp_path / "j", "0001aaaa", "LINE-PC-04", at=DAY_ONE)
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[2])
    payload["fields"]["step_id"] = "tampered"
    lines[2] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run = summarise_journal(path)
    assert not run.is_readable
    assert "altered" in run.damaged
    # What could be read is still there.
    assert run.terminal == "LINE-PC-04"


def test_a_journal_that_cannot_be_read_at_all_is_an_error(tmp_path):
    with pytest.raises(VerifyError, match="cannot read journal"):
        summarise_journal(tmp_path / "nope.jsonl")


def test_reading_a_directory(journals):
    history = read_history(journals)
    assert len(history) == 3
    assert history.root == journals


def test_runs_come_back_oldest_first(journals):
    history = read_history(journals)
    assert [run.run_id for run in history] == ["0001aaaa", "0002bbbb", "0003cccc"]


def test_terminals_are_listed(journals):
    assert read_history(journals).terminals == ["LINE-PC-04", "LINE-PC-05"]


def test_runs_for_one_terminal(journals):
    runs = read_history(journals).for_terminal("LINE-PC-04")
    assert [run.run_id for run in runs] == ["0001aaaa", "0003cccc"]


def test_a_terminal_with_no_runs(journals):
    assert read_history(journals).for_terminal("LINE-PC-99") == []


def test_the_latest_run_for_a_terminal(journals):
    assert read_history(journals).latest("LINE-PC-04").run_id == "0003cccc"
    assert read_history(journals).latest("LINE-PC-99") is None


def test_unfinished_runs_are_singled_out(journals):
    unfinished = read_history(journals).unfinished
    assert [run.run_id for run in unfinished] == ["0003cccc"]


def test_no_damaged_journals_in_a_clean_directory(journals):
    assert read_history(journals).damaged == []


def test_a_damaged_journal_shows_up_in_the_history(journals):
    path = journals / "run-0001aaaa.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    history = read_history(journals)
    assert len(history.damaged) == 1
    assert history.damaged[0].run_id == "0001aaaa"


def test_counts_by_outcome(journals):
    assert read_history(journals).counts() == {
        "complete": 1,
        "rolled-back": 1,
        "unfinished": 1,
    }


def test_counts_call_a_damaged_journal_unreadable(journals):
    path = journals / "run-0001aaaa.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(VerifyError):
        read_history(journals)


def test_lines_read_as_a_summary(journals):
    lines = read_history(journals).lines()
    assert "3 runs across 2 terminals" in lines[0]
    assert any("never finished" in line for line in lines)


def test_a_journal_with_no_start_time_sorts_last(tmp_path):
    root = tmp_path / "_journal"
    write_run(root, "0001aaaa", "LINE-PC-04", at=DAY_TWO)
    odd = root / "run-0000zzzz.jsonl"
    odd.write_text("", encoding="utf-8")
    history = read_history(root)
    assert [run.run_id for run in history] == ["0001aaaa", ""]


def test_reading_a_directory_that_is_not_there(tmp_path):
    with pytest.raises(VerifyError, match="does not exist"):
        read_history(tmp_path / "nope")


def test_reading_an_empty_directory(tmp_path):
    root = tmp_path / "_journal"
    root.mkdir()
    history = read_history(root)
    assert len(history) == 0
    assert history.terminals == []
    assert history.counts() == {}


def test_files_that_are_not_journals_are_ignored(journals):
    (journals / "notes.txt").write_text("not a journal", encoding="utf-8")
    assert len(read_history(journals)) == 3


def test_find_unfinished_returns_the_run(journals):
    run = find_unfinished(journals, "LINE-PC-04")
    assert run is not None
    assert run.run_id == "0003cccc"


def test_find_unfinished_on_a_clean_terminal(journals):
    assert find_unfinished(journals, "LINE-PC-05") is None


def test_find_unfinished_on_an_unknown_terminal(journals):
    assert find_unfinished(journals, "LINE-PC-99") is None


def test_find_unfinished_on_a_directory_that_is_not_there(tmp_path):
    assert find_unfinished(tmp_path / "nope", "LINE-PC-04") is None


def test_find_unfinished_picks_the_most_recent(tmp_path):
    root = tmp_path / "_journal"
    write_run(root, "0001aaaa", "LINE-PC-04", at=DAY_ONE, outcome=None)
    write_run(root, "0002bbbb", "LINE-PC-04", at=DAY_TWO, outcome=None)
    assert find_unfinished(root, "LINE-PC-04").run_id == "0002bbbb"


def test_describe_a_clean_run(tmp_path):
    path = write_run(tmp_path / "j", "0001aaaa", "LINE-PC-04", at=DAY_ONE)
    described = summarise_journal(path).describe()
    assert "LINE-PC-04" in described
    assert "complete" in described


def test_describe_a_damaged_one():
    run = RunSummary(path=__import__("pathlib").Path("run-x.jsonl"), damaged="chain broken")
    assert run.describe() == "run-x.jsonl: unreadable, chain broken"


def test_an_empty_history():
    history = History()
    assert len(history) == 0
    assert history.lines()[0].startswith("(in memory): 0 runs")
