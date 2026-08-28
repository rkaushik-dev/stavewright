from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stavewright.clock import utc
from stavewright.errors import PlanError
from stavewright.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from stavewright.fleet.schedule import (
    DEFAULT_CHANGEOVER,
    DEFAULT_PER_MACHINE,
    Window,
    check_windows,
    schedule_rollout,
    wave_duration,
)
from stavewright.fleet.waves import Rollout, Wave, plan_rollout
from stavewright.model.profile import TerminalProfile


def entry(terminal_id: str, line: str, position: int = 1, criticality=Criticality.NORMAL):
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=position,
        criticality=criticality,
        shift_pattern=ShiftPattern.DAY,
    )


def wave(number: int, *entries) -> Wave:
    return Wave(number=number, entries=entries, criticality=Criticality.NORMAL)


PLANT = Roster(
    plant="Wrexham",
    entries=(
        entry("BENCH-01", "bench", 1, Criticality.SPARE),
        entry("SAMPLE-01", "sample", 1),
        entry("EXPORT-01", "export", 1, Criticality.CRITICAL),
        entry("EXPORT-02", "export", 2, Criticality.CRITICAL),
    ),
)

NIGHT = Window(utc(2025, 6, 2, 22, 0), utc(2025, 6, 3, 2, 0), "monday night")
TUESDAY = Window(utc(2025, 6, 3, 22, 0), utc(2025, 6, 4, 2, 0), "tuesday night")


def test_a_window_knows_how_long_it_is():
    assert NIGHT.duration == timedelta(hours=4)


def test_a_window_needs_aware_times():
    with pytest.raises(PlanError, match="timezone aware"):
        Window(datetime(2025, 6, 2, 22), utc(2025, 6, 3, 2))


def test_a_window_that_ends_first_is_refused():
    with pytest.raises(PlanError, match="ends before it starts"):
        Window(utc(2025, 6, 3, 2), utc(2025, 6, 2, 22), "backwards")


def test_a_window_of_no_length_is_refused():
    with pytest.raises(PlanError, match="ends before it starts"):
        Window(utc(2025, 6, 3, 2), utc(2025, 6, 3, 2))


def test_a_window_contains_its_start_but_not_its_end():
    assert NIGHT.contains(NIGHT.start)
    assert not NIGHT.contains(NIGHT.end)


def test_a_moment_before_the_window():
    assert not NIGHT.contains(utc(2025, 6, 2, 21, 59))


def test_two_windows_that_do_not_touch():
    assert not NIGHT.overlaps(TUESDAY)


def test_a_window_overlaps_itself():
    assert NIGHT.overlaps(NIGHT)


def test_windows_that_share_an_hour_overlap():
    late = Window(utc(2025, 6, 3, 1), utc(2025, 6, 3, 5))
    assert NIGHT.overlaps(late)
    assert late.overlaps(NIGHT)


def test_windows_that_only_touch_do_not_overlap():
    next_up = Window(NIGHT.end, NIGHT.end + timedelta(hours=1))
    assert not NIGHT.overlaps(next_up)


def test_describing_a_window():
    text = NIGHT.describe()
    assert text.startswith("monday night: 2025-06-02 22:00")
    assert "(4.0h)" in text


def test_describing_an_unlabelled_window():
    assert Window(utc(2025, 6, 2, 22), utc(2025, 6, 3, 2)).describe().startswith("2025-06-02")


def test_one_machine_takes_the_default_plus_changeover():
    assert wave_duration(wave(1, entry("A-01", "one"))) == DEFAULT_PER_MACHINE + DEFAULT_CHANGEOVER


def test_three_machines_and_one_engineer_is_serial():
    three = wave(1, entry("A-01", "one"), entry("B-01", "two"), entry("C-01", "three"))
    assert wave_duration(three) == 3 * DEFAULT_PER_MACHINE + DEFAULT_CHANGEOVER


def test_three_machines_and_three_engineers_is_one_round():
    three = wave(1, entry("A-01", "one"), entry("B-01", "two"), entry("C-01", "three"))
    expected = DEFAULT_PER_MACHINE + DEFAULT_CHANGEOVER
    assert wave_duration(three, engineers=3) == expected


def test_three_machines_and_two_engineers_takes_two_rounds():
    three = wave(1, entry("A-01", "one"), entry("B-01", "two"), entry("C-01", "three"))
    expected = 2 * DEFAULT_PER_MACHINE + DEFAULT_CHANGEOVER
    assert wave_duration(three, engineers=2) == expected


def test_more_engineers_than_machines_changes_nothing():
    one = wave(1, entry("A-01", "one"))
    assert wave_duration(one, engineers=9) == wave_duration(one, engineers=1)


def test_no_engineers_is_refused():
    with pytest.raises(PlanError, match="at least one engineer"):
        wave_duration(wave(1, entry("A-01", "one")), engineers=0)


def test_the_per_machine_time_can_be_overridden():
    one = wave(1, entry("A-01", "one"))
    assert wave_duration(one, per_machine=timedelta(hours=1), changeover=timedelta()) == timedelta(
        hours=1
    )


def test_windows_come_back_sorted():
    assert check_windows([TUESDAY, NIGHT]) == [NIGHT, TUESDAY]


def test_no_windows_is_not_an_error():
    assert check_windows([]) == []


def test_overlapping_windows_are_refused():
    with pytest.raises(PlanError, match="windows overlap"):
        check_windows([NIGHT, Window(utc(2025, 6, 3, 1), utc(2025, 6, 3, 5), "late")])


def test_the_overlap_error_names_both():
    with pytest.raises(PlanError) as caught:
        check_windows([NIGHT, Window(utc(2025, 6, 3, 1), utc(2025, 6, 3, 5), "late")])
    assert "monday night" in caught.value.context["first"]
    assert "late" in caught.value.context["second"]


def test_everything_fits_one_long_night():
    plan = plan_rollout(PLANT)
    schedule = schedule_rollout(plan, [NIGHT])
    assert schedule.is_complete
    assert len(schedule) == len(plan)


def test_the_schedule_carries_the_plant():
    assert schedule_rollout(plan_rollout(PLANT), [NIGHT]).plant == "Wrexham"


def test_the_first_wave_starts_when_the_window_opens():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    assert schedule.bookings[0].start == NIGHT.start


def test_waves_run_back_to_back():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    for earlier, later in zip(schedule.bookings, schedule.bookings[1:], strict=False):
        assert later.start == earlier.end


def test_every_booking_stays_inside_its_window():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    for booking in schedule:
        assert booking.window.contains(booking.start)
        assert booking.end <= booking.window.end


def test_the_schedule_says_when_it_finishes():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    assert schedule.finishes_at == schedule.bookings[-1].end


def test_an_empty_schedule_finishes_nowhere():
    empty = schedule_rollout(Rollout(plant="W"), [NIGHT])
    assert schedule_is_empty(empty)


def schedule_is_empty(schedule) -> bool:
    return schedule.finishes_at is None and len(schedule) == 0 and schedule.is_complete


def test_the_working_time_adds_up():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    assert schedule.working_time == sum(
        (booking.duration for booking in schedule), timedelta()
    )


def test_a_wave_that_does_not_fit_waits_for_the_next_window():
    short = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 23), "short")
    plan = plan_rollout(PLANT)
    schedule = schedule_rollout(plan, [short, TUESDAY])
    assert len(schedule.in_window("short")) == 1
    assert len(schedule.in_window("tuesday night")) == len(plan) - 1


def test_a_wave_never_spans_two_windows():
    short = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 30), "short")
    schedule = schedule_rollout(plan_rollout(PLANT), [short, TUESDAY])
    assert schedule.in_window("short") == []


def test_waves_that_fit_nowhere_come_back_unplaced():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    plan = plan_rollout(PLANT)
    schedule = schedule_rollout(plan, [tiny])
    assert not schedule.is_complete
    assert len(schedule.unplaced) == len(plan)


def test_with_no_windows_nothing_is_placed():
    plan = plan_rollout(PLANT)
    schedule = schedule_rollout(plan, [])
    assert len(schedule) == 0
    assert len(schedule.unplaced) == len(plan)


def test_waves_keep_their_order_even_when_a_later_one_would_fit():
    # Wave 1 is too big for the first window and wave 2 would fit. It still
    # waits, because the order is what makes the spares go first.
    one_slot = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 40), "one slot")
    plan = plan_rollout(PLANT, batch_limit=1)
    schedule = schedule_rollout(plan, [one_slot, TUESDAY], per_machine=timedelta(minutes=30))
    assert [booking.wave.number for booking in schedule] == [
        wave.number for wave in plan
    ]


def test_a_later_wave_does_not_backfill_a_gap():
    first = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 23), "first")
    plan = plan_rollout(PLANT)
    schedule = schedule_rollout(plan, [first, TUESDAY])
    booked_first = [booking.wave.number for booking in schedule.in_window("first")]
    assert booked_first == [1]


def test_more_engineers_fit_more_in():
    plant = Roster(plant="W", entries=tuple(entry(f"L{n}-01", f"line{n}") for n in range(1, 4)))
    plan = plan_rollout(plant)
    short = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 45), "short")
    assert not schedule_rollout(plan, [short]).is_complete
    assert schedule_rollout(plan, [short], engineers=3).is_complete


def test_describing_a_booking():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    text = schedule.bookings[0].describe()
    assert text.startswith("wave 1 at 2025-06-02 22:00")
    assert "BENCH-01" in text
    assert "35m" in text


def test_describing_a_schedule_lists_the_unplaced():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    lines = schedule_rollout(plan_rollout(PLANT), [tiny]).describe()
    assert all("does not fit any window" in line for line in lines)


def test_the_windows_do_not_have_to_arrive_sorted():
    plan = plan_rollout(PLANT, batch_limit=1)
    forwards = schedule_rollout(plan, [NIGHT, TUESDAY])
    backwards = schedule_rollout(plan, [TUESDAY, NIGHT])
    assert [b.start for b in forwards] == [b.start for b in backwards]


def test_overlapping_windows_stop_the_schedule():
    with pytest.raises(PlanError, match="windows overlap"):
        schedule_rollout(plan_rollout(PLANT), [NIGHT, NIGHT])


def test_iterating_a_schedule_yields_bookings():
    schedule = schedule_rollout(plan_rollout(PLANT), [NIGHT])
    assert [booking.wave.number for booking in schedule] == [
        booking.wave.number for booking in schedule.bookings
    ]
