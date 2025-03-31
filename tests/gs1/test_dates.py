from __future__ import annotations

from datetime import date

import pytest

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.dates import (
    Gs1Date,
    apply_century_window,
    format_date,
    is_expired,
    parse_date,
    parse_date_time,
)

# Every element string in the shipped GS1-1.txt carries (15)150807.
SHIPPED_BEST_BEFORE = "150807"


def test_the_date_in_the_shipped_template_data():
    parsed = parse_date(SHIPPED_BEST_BEFORE, reference_year=2016)
    assert parsed.year == 2015
    assert parsed.month == 8
    assert parsed.day == 7
    assert parsed.to_date() == date(2015, 8, 7)


def test_that_date_read_today_still_lands_in_2015():
    assert parse_date(SHIPPED_BEST_BEFORE, reference_year=2025).year == 2015


def test_round_trip():
    parsed = parse_date(SHIPPED_BEST_BEFORE, reference_year=2016)
    assert parsed.format() == SHIPPED_BEST_BEFORE


@pytest.mark.parametrize(
    ("two_digit", "reference", "expected"),
    [
        (25, 2025, 2025),
        (0, 2025, 2000),
        (49, 2025, 2049),
        (75, 2025, 2075),
        (76, 2025, 1976),
        (99, 2025, 1999),
        (15, 2016, 2015),
        (99, 2000, 1999),
        (0, 1999, 2000),
    ],
)
def test_the_century_window(two_digit, reference, expected):
    assert apply_century_window(two_digit, reference) == expected


def test_the_window_boundary_is_fifty_one_years_back():
    # 2025 + 51 = 76, so 76 flips to the previous century and 75 does not.
    assert apply_century_window(75, 2025) == 2075
    assert apply_century_window(76, 2025) == 1976


def test_the_window_boundary_going_forward():
    # The forward flip needs a difference of -50, which only happens with a
    # reference year in the second half of a century. From 2075, YY 25 is fifty
    # behind and lands in 2125, while YY 26 is only forty nine behind and stays.
    assert apply_century_window(25, 2075) == 2125
    assert apply_century_window(26, 2075) == 2026


def test_the_window_is_asymmetric():
    # Fifty forward stays in the current century, fifty one forward does not.
    # Fifty back flips, forty nine back does not. The window is 50 forward and
    # 49 back, not fifty each way.
    assert apply_century_window(75, 2025) == 2075
    assert apply_century_window(76, 2025) == 1976
    assert apply_century_window(25, 2075) == 2125
    assert apply_century_window(26, 2075) == 2026


@pytest.mark.parametrize("value", [-1, 100, 1000])
def test_an_out_of_range_two_digit_year_is_refused(value):
    with pytest.raises(Gs1Error, match="out of range"):
        apply_century_window(value, 2025)


def test_day_zero_zero_means_end_of_month():
    parsed = parse_date("250200", reference_year=2025)
    assert parsed.is_end_of_month
    assert parsed.day is None
    assert parsed.to_date() == date(2025, 2, 28)


def test_end_of_month_is_leap_year_aware():
    assert parse_date("240200", reference_year=2024).to_date() == date(2024, 2, 29)


@pytest.mark.parametrize(
    ("value", "reference", "last_day"),
    [
        ("250100", 2025, 31),
        ("250400", 2025, 30),
        ("250600", 2025, 30),
        ("251200", 2025, 31),
    ],
)
def test_end_of_month_for_each_month_length(value, reference, last_day):
    assert parse_date(value, reference_year=reference).to_date().day == last_day


def test_end_of_month_round_trips():
    assert parse_date("250200", reference_year=2025).format() == "250200"


def test_the_twenty_ninth_of_february_is_fine_in_a_leap_year():
    assert parse_date("240229", reference_year=2024).to_date() == date(2024, 2, 29)


def test_the_twenty_ninth_of_february_is_refused_in_a_common_year():
    with pytest.raises(Gs1Error, match="day that month does not have"):
        parse_date("250229", reference_year=2025)


def test_the_thirty_first_of_april_is_refused():
    with pytest.raises(Gs1Error, match="day that month does not have") as excinfo:
        parse_date("250431", reference_year=2025)
    assert excinfo.value.context["last"] == 30


@pytest.mark.parametrize("value", ["250013", "251301", "259901"])
def test_an_impossible_month_is_refused(value):
    with pytest.raises(Gs1Error, match="impossible month"):
        parse_date(value, reference_year=2025)


@pytest.mark.parametrize("value", ["", "2508", "25080", "2508077"])
def test_a_wrong_length_is_refused(value):
    with pytest.raises(Gs1Error, match="must be 6 digits"):
        parse_date(value, reference_year=2025)


@pytest.mark.parametrize("value", ["25AUG7", "2508 7", "25-8-7"])
def test_non_digits_are_refused(value):
    with pytest.raises(Gs1Error, match="digits only"):
        parse_date(value, reference_year=2025)


def test_parsing_without_a_reference_year_uses_today():
    parsed = parse_date(format_date(date.today()))
    assert parsed.to_date() == date.today()


def test_format_a_date():
    assert format_date(date(2015, 8, 7)) == "150807"
    assert format_date(date(2025, 12, 31)) == "251231"


def test_format_as_end_of_month():
    assert format_date(date(2025, 2, 14), end_of_month=True) == "250200"


def test_str_of_a_concrete_day_is_iso():
    assert str(parse_date("150807", reference_year=2016)) == "2015-08-07"


def test_str_of_an_end_of_month_says_so():
    assert str(parse_date("250200", reference_year=2025)) == "2025-02-end"


def test_date_time_parsing():
    parsed, hour, minute = parse_date_time("1508071430", reference_year=2016)
    assert parsed.to_date() == date(2015, 8, 7)
    assert (hour, minute) == (14, 30)


def test_midnight_is_valid():
    _, hour, minute = parse_date_time("1508070000", reference_year=2016)
    assert (hour, minute) == (0, 0)


def test_a_wrong_length_date_time_is_refused():
    with pytest.raises(Gs1Error, match="must be 10 digits"):
        parse_date_time("150807", reference_year=2016)


def test_an_out_of_range_hour_is_refused():
    with pytest.raises(Gs1Error, match="hour is out of range"):
        parse_date_time("1508072400", reference_year=2016)


def test_an_out_of_range_minute_is_refused():
    with pytest.raises(Gs1Error, match="minute is out of range"):
        parse_date_time("1508071360", reference_year=2016)


def test_expiry_in_the_past():
    assert is_expired(Gs1Date(2015, 8, 7), today=date(2025, 1, 1))


def test_expiry_in_the_future():
    assert not is_expired(Gs1Date(2030, 1, 1), today=date(2025, 1, 1))


def test_expiry_today_has_not_expired():
    assert not is_expired(Gs1Date(2025, 1, 1), today=date(2025, 1, 1))


def test_end_of_month_expiry_uses_the_last_day():
    # An end of February date is still good on the twenty eighth.
    assert not is_expired(Gs1Date(2025, 2, None), today=date(2025, 2, 28))
    assert is_expired(Gs1Date(2025, 2, None), today=date(2025, 3, 1))
