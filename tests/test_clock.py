from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stavewright import clock

PINNED = datetime(2026, 3, 14, 9, 26, 53, tzinfo=UTC)


def test_now_is_timezone_aware():
    assert clock.now().tzinfo is not None


def test_now_is_utc():
    assert clock.now().utcoffset() == timedelta(0)


def test_now_moves():
    first = clock.now()
    second = clock.now()
    assert second >= first


def test_stamp_is_an_iso_string():
    text = clock.stamp()
    assert datetime.fromisoformat(text).tzinfo is not None


def test_freezing_pins_the_clock():
    with clock.frozen(PINNED):
        assert clock.now() == PINNED
        assert clock.now() == PINNED
        assert clock.stamp() == PINNED.isoformat()


def test_the_clock_runs_again_afterwards():
    with clock.frozen(PINNED):
        pass
    assert clock.now() != PINNED


def test_frozen_yields_what_it_pinned():
    with clock.frozen(PINNED) as pinned:
        assert pinned == PINNED


def test_is_frozen_tracks_the_block():
    assert not clock.is_frozen()
    with clock.frozen(PINNED):
        assert clock.is_frozen()
    assert not clock.is_frozen()


def test_freezes_nest_and_restore():
    later = PINNED + timedelta(days=1)
    with clock.frozen(PINNED):
        with clock.frozen(later):
            assert clock.now() == later
        assert clock.now() == PINNED
    assert not clock.is_frozen()


def test_the_clock_is_restored_after_an_exception():
    with pytest.raises(RuntimeError), clock.frozen(PINNED):
        raise RuntimeError("something went wrong")
    assert not clock.is_frozen()


def test_a_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="timezone aware"), clock.frozen(
        datetime(2026, 3, 14, 9, 26)
    ):
        pass


def test_the_utc_helper():
    assert clock.utc(2026, 3, 14) == datetime(2026, 3, 14, tzinfo=UTC)
    assert clock.utc(2026, 3, 14, 9, 26) == datetime(2026, 3, 14, 9, 26, tzinfo=UTC)


# Everything that stamps something goes through this module now, so freezing
# the clock is enough to make any of them reproducible.


def test_a_manifest_stamp_follows_the_clock():
    from stavewright.model.manifest import Manifest, utc_now

    with clock.frozen(PINNED):
        assert utc_now() == PINNED
        assert Manifest(generated_at=utc_now()).generated_at == PINNED


def test_a_plan_stamp_follows_the_clock(profile):
    from stavewright.plan.steps import Plan, utc_now

    with clock.frozen(PINNED):
        plan = Plan(profile=profile, created_at=utc_now())
    assert plan.created_at == PINNED


def test_a_report_stamp_follows_the_clock():
    from stavewright.report.document import Report

    with clock.frozen(PINNED):
        assert Report(title="x").stamp().generated_at == PINNED


def test_a_signature_stamp_follows_the_clock():
    from stavewright.model.manifest import Manifest
    from stavewright.signing import generate_key, sign_manifest

    with clock.frozen(PINNED):
        assert sign_manifest(Manifest(), generate_key()).signed_at == PINNED


def test_every_journal_record_follows_the_clock(tmp_path):
    from stavewright.apply.journal import Journal, RecordKind, read_journal

    path = tmp_path / "run.jsonl"
    with clock.frozen(PINNED), Journal(path) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "run0001")
        journal.append(RecordKind.STEP_OK, step_id="one")
        journal.end("complete")

    records = read_journal(path)
    assert len(records) == 3
    assert {record.at for record in records} == {PINNED.isoformat()}


def test_two_frozen_runs_produce_the_same_journal_bytes(tmp_path):
    from stavewright.apply.journal import Journal, RecordKind

    written = []
    for name in ("one.jsonl", "two.jsonl"):
        path = tmp_path / name
        with clock.frozen(PINNED), Journal(path) as journal:
            journal.begin("sha256:abc", "LINE-PC-04", "run0001")
            journal.append(RecordKind.STEP_OK, step_id="one")
            journal.end("complete")
        written.append(path.read_text(encoding="utf-8"))
    assert written[0] == written[1]


def test_the_dead_cli_helpers_are_gone():
    from stavewright.cli import common

    assert not hasattr(common, "outcome_line")
    assert not hasattr(common, "is_a_terminal")
    assert "outcome_line" not in common.__all__
    assert "is_a_terminal" not in common.__all__
