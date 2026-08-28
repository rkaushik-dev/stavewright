from __future__ import annotations

import json

import pytest

from stavewright import logging as sw_logging


@pytest.fixture(autouse=True)
def _clean_logging():
    sw_logging.reset()
    yield
    sw_logging.reset()


def test_run_ids_are_twelve_hex_characters():
    run_id = sw_logging.new_run_id()
    assert len(run_id) == 12
    assert all(c in "0123456789abcdef" for c in run_id)


def test_run_ids_do_not_repeat():
    ids = {sw_logging.new_run_id() for _ in range(500)}
    assert len(ids) == 500


def test_is_configured_tracks_configure_and_reset(tmp_path):
    assert not sw_logging.is_configured()
    sw_logging.configure(console=False, log_file=tmp_path / "run.jsonl")
    assert sw_logging.is_configured()
    sw_logging.reset()
    assert not sw_logging.is_configured()


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError, match="unknown log level"):
        sw_logging.configure(level="CHATTY", console=False)


@pytest.mark.parametrize("level", list(sw_logging.LEVELS))
def test_every_advertised_level_is_accepted(level, tmp_path):
    sw_logging.configure(level=level, console=False, log_file=tmp_path / f"{level}.jsonl")


def test_level_is_case_insensitive(tmp_path):
    sw_logging.configure(level="debug", console=False, log_file=tmp_path / "run.jsonl")
    assert sw_logging.is_configured()


def _read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_file_sink_writes_one_json_object_per_event(tmp_path):
    log_file = tmp_path / "logs" / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    log = sw_logging.get_logger("commission")
    log.info("staging started", items=42)
    log.warning("ocx already registered", clsid="{8A83879F}")

    events = _read_events(log_file)
    assert [e["event"] for e in events] == ["staging started", "ocx already registered"]
    assert events[0]["items"] == 42
    assert events[0]["level"] == "info"
    assert events[1]["level"] == "warning"


def test_file_sink_creates_its_parent_directory(tmp_path):
    log_file = tmp_path / "deep" / "nested" / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    sw_logging.get_logger("t").info("hello")
    assert log_file.exists()


def test_events_carry_an_iso_timestamp(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    sw_logging.get_logger("t").info("hello")
    stamp = _read_events(log_file)[0]["timestamp"]
    assert stamp.endswith("Z")
    assert "T" in stamp


def test_logger_name_is_recorded(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    sw_logging.get_logger("stavewright.stage").info("hello")
    assert _read_events(log_file)[0]["logger"] == "stavewright.stage"


def test_level_filters_quieter_events(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(level="WARNING", console=False, log_file=log_file)
    log = sw_logging.get_logger("t")
    log.info("not recorded")
    log.error("recorded")
    assert [e["event"] for e in _read_events(log_file)] == ["recorded"]


def test_run_context_binds_the_run_id(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    log = sw_logging.get_logger("t")
    with sw_logging.run_context("abc123def456"):
        log.info("inside")
    log.info("outside")

    events = _read_events(log_file)
    assert events[0]["run_id"] == "abc123def456"
    assert "run_id" not in events[1]


def test_run_context_binds_the_terminal_when_given(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    with sw_logging.run_context("abc123def456", terminal="LINE-PC-04"):
        sw_logging.get_logger("t").info("inside")
    assert _read_events(log_file)[0]["terminal"] == "LINE-PC-04"


def test_run_context_omits_the_terminal_when_not_given(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    with sw_logging.run_context("abc123def456"):
        sw_logging.get_logger("t").info("inside")
    assert "terminal" not in _read_events(log_file)[0]


def test_run_context_takes_extra_bindings(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    with sw_logging.run_context("abc123def456", plan="9f86d0818839"):
        sw_logging.get_logger("t").info("inside")
    assert _read_events(log_file)[0]["plan"] == "9f86d0818839"


def test_run_context_yields_the_run_id():
    with sw_logging.run_context("abc123def456") as run_id:
        assert run_id == "abc123def456"


def test_run_context_unbinds_after_an_exception(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    log = sw_logging.get_logger("t")
    with pytest.raises(RuntimeError), sw_logging.run_context("abc123def456"):
        raise RuntimeError("apply failed")
    log.info("after")
    assert "run_id" not in _read_events(log_file)[0]


def test_nested_run_contexts_restore_the_outer_binding(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    log = sw_logging.get_logger("t")
    with sw_logging.run_context("outer0000000"):
        with sw_logging.run_context("inner0000000"):
            log.info("inner")
        log.info("outer")
    events = _read_events(log_file)
    assert events[0]["run_id"] == "inner0000000"
    assert events[1]["run_id"] == "outer0000000"


def test_configure_twice_does_not_duplicate_events(tmp_path):
    log_file = tmp_path / "run.jsonl"
    sw_logging.configure(console=False, log_file=log_file)
    sw_logging.configure(console=False, log_file=log_file)
    sw_logging.get_logger("t").info("once")
    assert len(_read_events(log_file)) == 1
