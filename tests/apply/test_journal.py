from __future__ import annotations

import json

import pytest

from stavewright.apply.journal import (
    GENESIS,
    Journal,
    Record,
    RecordKind,
    digest_for,
    iter_steps,
    parse_line,
    read_journal,
    read_records,
    replay,
    verify_chain,
)
from stavewright.errors import JournalError


@pytest.fixture
def journal_path(tmp_path):
    return tmp_path / "_journal" / "run.jsonl"


def write_run(path, *, fail_at: str | None = None, rollback: bool = False):
    with Journal(path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "0123456789ab")
        for step in ("vcredist", "sxs.mfc", "ocx.c1sizer", "reg.productinfo"):
            journal.append(RecordKind.STEP_START, step_id=step)
            if step == fail_at:
                journal.append(RecordKind.STEP_FAILED, step_id=step, detail="regsvr32 said no")
                break
            journal.append(RecordKind.STEP_OK, step_id=step, changed=True)
        if rollback:
            for step in ("sxs.mfc", "vcredist"):
                journal.append(RecordKind.ROLLBACK_OK, step_id=step)
        journal.end("failed" if fail_at else "complete")


def test_a_journal_creates_its_parent_directory(journal_path):
    with Journal(journal_path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "run")
    assert journal_path.exists()


def test_one_json_object_per_line(journal_path):
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line) for line in lines)


def test_sequence_numbers_start_at_zero_and_count_up(journal_path):
    write_run(journal_path)
    records = read_records(journal_path)
    assert [r.sequence for r in records] == list(range(len(records)))


def test_the_first_record_chains_to_the_genesis_hash(journal_path):
    write_run(journal_path)
    assert read_records(journal_path)[0].previous == GENESIS


def test_a_clean_journal_verifies(journal_path):
    write_run(journal_path)
    verify_chain(read_records(journal_path))


def test_read_journal_verifies_by_default(journal_path):
    write_run(journal_path)
    assert read_journal(journal_path)


def test_a_truncated_journal_still_verifies(journal_path):
    # Losing the tail is expected. What must not verify is a journal whose
    # middle has been altered.
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    journal_path.write_text("\n".join(lines[:4]) + "\n", encoding="utf-8")
    verify_chain(read_records(journal_path))


def test_an_altered_record_is_caught(journal_path):
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[2])
    payload["fields"]["step_id"] = "something-else"
    lines[2] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalError, match="has been altered"):
        read_journal(journal_path)


def test_a_removed_record_in_the_middle_is_caught(journal_path):
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    del lines[3]
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalError, match="sequence has a gap"):
        read_journal(journal_path)


def test_a_reordered_journal_is_caught(journal_path):
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    lines[2], lines[3] = lines[3], lines[2]
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalError, match="sequence has a gap"):
        read_journal(journal_path)


def test_a_broken_chain_with_intact_sequence_is_caught(journal_path):
    write_run(journal_path)
    records = read_records(journal_path)
    tampered = Record(
        sequence=records[2].sequence,
        kind=records[2].kind,
        at=records[2].at,
        previous=GENESIS,
        digest="",
        fields=records[2].fields,
    )
    tampered = Record(
        sequence=tampered.sequence,
        kind=tampered.kind,
        at=tampered.at,
        previous=tampered.previous,
        digest=digest_for(tampered.body()),
        fields=tampered.fields,
    )
    records[2] = tampered
    with pytest.raises(JournalError, match="does not chain"):
        verify_chain(records)


def test_verification_can_be_skipped(journal_path):
    write_run(journal_path)
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    del lines[3]
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert read_journal(journal_path, verify=False)


def test_blank_lines_are_ignored(journal_path):
    write_run(journal_path)
    text = journal_path.read_text(encoding="utf-8")
    journal_path.write_text(text + "\n\n", encoding="utf-8")
    verify_chain(read_records(journal_path))


def test_appending_to_a_closed_journal_is_refused(journal_path):
    journal = Journal(journal_path)
    with pytest.raises(JournalError, match="not open"):
        journal.append(RecordKind.STEP_OK, step_id="x")


def test_closing_twice_is_safe(journal_path):
    journal = Journal(journal_path)
    journal.__enter__()
    journal.close()
    journal.close()


def test_the_head_advances_with_each_record(journal_path):
    with Journal(journal_path) as journal:
        assert journal.head == GENESIS
        first = journal.begin("sha256:abc", "LINE-PC-04", "run")
        assert journal.head == first.digest
        second = journal.append(RecordKind.STEP_OK, step_id="x")
        assert journal.head == second.digest
        assert journal.sequence == 2


def test_a_journal_line_that_is_not_json_is_caught():
    with pytest.raises(JournalError, match="not valid JSON"):
        parse_line("{not json", 1)


def test_a_journal_line_that_is_not_an_object_is_caught():
    with pytest.raises(JournalError, match="not an object"):
        parse_line("[1,2,3]", 1)


def test_a_journal_line_missing_fields_is_caught():
    with pytest.raises(JournalError, match="missing fields") as excinfo:
        parse_line('{"sequence": 0}', 1)
    assert "kind" in excinfo.value.context["missing"]


def test_an_unknown_record_kind_is_caught():
    line = json.dumps(
        {"sequence": 0, "kind": "teleport", "at": "x", "previous": GENESIS, "digest": "x"}
    )
    with pytest.raises(JournalError, match="unknown record kind"):
        parse_line(line, 1)


def test_reading_a_missing_journal_is_reported(tmp_path):
    with pytest.raises(JournalError, match="cannot read journal"):
        read_records(tmp_path / "nope.jsonl")


def test_replaying_a_clean_run(journal_path):
    write_run(journal_path)
    state = replay(read_journal(journal_path))
    assert state.run_id == "0123456789ab"
    assert state.terminal == "LINE-PC-04"
    assert state.plan_digest == "sha256:abc"
    assert state.completed == ["vcredist", "sxs.mfc", "ocx.c1sizer", "reg.productinfo"]
    assert state.failed is None
    assert state.finished
    assert state.is_complete
    assert state.outcome == "complete"


def test_replaying_a_failed_run(journal_path):
    write_run(journal_path, fail_at="ocx.c1sizer")
    state = replay(read_journal(journal_path))
    assert state.completed == ["vcredist", "sxs.mfc"]
    assert state.failed == "ocx.c1sizer"
    assert state.failure_detail == "regsvr32 said no"
    assert not state.is_complete
    assert state.outcome == "failed"


def test_rollback_order_is_the_reverse_of_apply(journal_path):
    write_run(journal_path, fail_at="ocx.c1sizer")
    state = replay(read_journal(journal_path))
    assert state.needs_rollback == ["sxs.mfc", "vcredist"]


def test_steps_already_rolled_back_are_not_repeated(journal_path):
    write_run(journal_path, fail_at="ocx.c1sizer", rollback=True)
    state = replay(read_journal(journal_path))
    assert state.rolled_back == ["sxs.mfc", "vcredist"]
    assert state.needs_rollback == []


def test_a_partially_rolled_back_run_resumes_where_it_stopped(journal_path):
    with Journal(journal_path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "run")
        for step in ("a", "b", "c"):
            journal.append(RecordKind.STEP_OK, step_id=step)
        journal.append(RecordKind.STEP_FAILED, step_id="d", detail="boom")
        journal.append(RecordKind.ROLLBACK_OK, step_id="c")
    state = replay(read_journal(journal_path))
    assert state.needs_rollback == ["b", "a"]


def test_skipped_steps_are_tracked_separately(journal_path):
    with Journal(journal_path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "run")
        journal.append(RecordKind.STEP_OK, step_id="a")
        journal.append(RecordKind.STEP_SKIPPED, step_id="b", reason="already satisfied")
        journal.end("complete")
    state = replay(read_journal(journal_path))
    assert state.completed == ["a"]
    assert state.skipped == ["b"]
    assert state.needs_rollback == ["a"]


def test_an_unfinished_run_is_not_complete(journal_path):
    with Journal(journal_path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "run")
        journal.append(RecordKind.STEP_OK, step_id="a")
    state = replay(read_journal(journal_path))
    assert not state.finished
    assert not state.is_complete
    assert state.needs_rollback == ["a"]


def test_replaying_an_empty_journal():
    state = replay([])
    assert state.completed == []
    assert not state.finished
    assert state.needs_rollback == []


def test_iter_steps_yields_outcomes(journal_path):
    write_run(journal_path, fail_at="ocx.c1sizer")
    outcomes = dict(iter_steps(read_journal(journal_path)))
    assert outcomes["vcredist"] is RecordKind.STEP_OK
    assert outcomes["ocx.c1sizer"] is RecordKind.STEP_FAILED


def test_a_record_describes_itself():
    record = Record(
        sequence=7,
        kind=RecordKind.STEP_OK,
        at="2025-05-29T09:00:00+00:00",
        previous=GENESIS,
        digest="x",
        fields={"step_id": "ocx.c1sizer"},
    )
    assert record.describe() == "   7 step-ok ocx.c1sizer"


def test_a_record_without_a_step_describes_itself():
    record = Record(
        sequence=0, kind=RecordKind.BEGIN, at="x", previous=GENESIS, digest="x"
    )
    assert record.describe() == "   0 begin"


def test_appending_to_an_existing_journal_starts_a_new_chain(journal_path):
    # A second run against the same path is a second chain, and verify_chain
    # will say so. Runs get their own file, and this test pins the behaviour so
    # nobody assumes otherwise.
    write_run(journal_path)
    write_run(journal_path)
    with pytest.raises(JournalError, match="sequence has a gap"):
        read_journal(journal_path)
