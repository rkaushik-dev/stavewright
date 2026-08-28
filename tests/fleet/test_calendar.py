from __future__ import annotations

import tomllib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from stavewright.errors import ConfigError, PlanError
from stavewright.fleet.schedule import parse_windows, read_windows

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


def windows(document: str = CALENDAR):
    return parse_windows(tomllib.loads(document))


def test_reading_two_windows():
    assert len(windows()) == 2


def test_the_labels_come_through():
    assert [window.label for window in windows()] == ["monday night", "tuesday night"]


def test_the_times_are_utc():
    first = windows()[0]
    assert first.start == datetime(2025, 6, 2, 22, tzinfo=UTC)
    assert first.end == datetime(2025, 6, 3, 2, tzinfo=UTC)


def test_the_durations_come_out_right():
    assert all(window.duration == timedelta(hours=4) for window in windows())


def test_a_document_with_no_windows_is_empty():
    assert parse_windows({"plant": "Wrexham"}) == []


def test_windows_come_back_sorted():
    swapped = "\n".join(
        [
            "[[window]]",
            'label = "later"',
            "start = 2025-06-03T22:00:00Z",
            "end = 2025-06-04T02:00:00Z",
            "",
            "[[window]]",
            'label = "earlier"',
            "start = 2025-06-02T22:00:00Z",
            "end = 2025-06-03T02:00:00Z",
        ]
    )
    assert [window.label for window in windows(swapped)] == ["earlier", "later"]


def test_a_window_with_no_label_is_allowed():
    document = CALENDAR.replace('label = "monday night"\n', "")
    assert windows(document)[0].label == ""


def test_an_offset_that_is_not_utc_is_kept():
    document = CALENDAR.replace("2025-06-02T22:00:00Z", "2025-06-02T23:00:00+01:00")
    first = windows(document)[0]
    assert first.start.utcoffset() == timedelta(hours=1)
    assert first.start == datetime(2025, 6, 2, 22, tzinfo=UTC)


def test_windows_are_comparable_across_offsets():
    document = CALENDAR.replace("2025-06-03T02:00:00Z", "2025-06-03T03:00:00+01:00")
    assert windows(document)[0].duration == timedelta(hours=4)


def test_a_window_list_that_is_not_a_list_is_refused():
    with pytest.raises(ConfigError, match="not a list of tables"):
        parse_windows({"window": {"label": "one"}})


def test_a_window_entry_that_is_not_a_table_is_refused():
    with pytest.raises(ConfigError, match="a window entry is not a table"):
        parse_windows({"window": ["monday night"]})


def test_a_window_with_no_start_is_refused():
    document = CALENDAR.replace("start = 2025-06-02T22:00:00Z\n", "")
    with pytest.raises(ConfigError, match="needs a start time"):
        windows(document)


def test_a_window_with_no_end_is_refused():
    document = CALENDAR.replace("end = 2025-06-03T02:00:00Z\n", "")
    with pytest.raises(ConfigError, match="needs a end time"):
        windows(document)


def test_the_error_names_which_window():
    document = CALENDAR.replace("end = 2025-06-04T02:00:00Z", "end = true")
    with pytest.raises(ConfigError) as caught:
        windows(document)
    assert caught.value.context["position"] == 2


def test_a_start_that_is_a_date_rather_than_a_time_is_refused():
    document = CALENDAR.replace("start = 2025-06-02T22:00:00Z", "start = 2025-06-02")
    with pytest.raises(ConfigError, match="needs a start time"):
        windows(document)


def test_a_local_time_with_no_offset_is_refused():
    # TOML allows it. Accepting it means the schedule reads differently
    # depending on which laptop opened the file.
    document = CALENDAR.replace("2025-06-02T22:00:00Z", "2025-06-02T22:00:00")
    with pytest.raises(ConfigError, match="needs an offset"):
        windows(document)


def test_the_refusal_says_which_field_had_no_offset():
    document = CALENDAR.replace("2025-06-03T02:00:00Z", "2025-06-03T02:00:00")
    with pytest.raises(ConfigError, match="end time needs an offset"):
        windows(document)


def test_a_backwards_window_is_refused():
    document = CALENDAR.replace("start = 2025-06-02T22:00:00Z", "start = 2025-06-03T04:00:00Z")
    with pytest.raises(PlanError, match="ends before it starts"):
        windows(document)


def test_overlapping_windows_are_refused():
    document = CALENDAR.replace("start = 2025-06-03T22:00:00Z", "start = 2025-06-03T01:00:00Z")
    with pytest.raises(PlanError, match="windows overlap"):
        windows(document)


def test_overlap_is_caught_across_offsets():
    document = CALENDAR.replace("start = 2025-06-03T22:00:00Z", "start = 2025-06-03T02:00:00+02:00")
    with pytest.raises(PlanError, match="windows overlap"):
        windows(document)


def test_windows_that_only_touch_are_fine():
    document = CALENDAR.replace("start = 2025-06-03T22:00:00Z", "start = 2025-06-03T02:00:00Z")
    assert len(windows(document)) == 2


def test_reading_a_calendar_from_disk(tmp_path):
    path = tmp_path / "calendar.toml"
    path.write_text(CALENDAR, encoding="utf-8")
    assert len(read_windows(path)) == 2


def test_a_missing_calendar_says_so(tmp_path):
    with pytest.raises(ConfigError, match="cannot read maintenance calendar"):
        read_windows(tmp_path / "gone.toml")


def test_a_calendar_that_is_not_toml_says_so(tmp_path):
    path = tmp_path / "calendar.toml"
    path.write_text("[[window", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        read_windows(path)


def test_the_error_names_the_file(tmp_path):
    path = tmp_path / "calendar.toml"
    path.write_text("[[window]]\nlabel = 'no times'\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        read_windows(path)
    assert caught.value.context["where"] == str(path)


def test_a_far_east_plant_calendar():
    # Nothing about this is special, which is the point. The offset survives
    # and everything downstream compares in absolute time.
    document = "\n".join(
        [
            "[[window]]",
            'label = "shift change"',
            "start = 2025-06-03T02:00:00+09:00",
            "end = 2025-06-03T06:00:00+09:00",
        ]
    )
    window = parse_windows(tomllib.loads(document))[0]
    assert window.start.tzinfo == timezone(timedelta(hours=9))
    assert window.duration == timedelta(hours=4)
