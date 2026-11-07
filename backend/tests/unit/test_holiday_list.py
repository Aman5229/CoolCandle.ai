"""The holiday list is copied by hand from NSE's circulars, so it is pinned here against them."""

from datetime import date

from coolcandle import seeds
from coolcandle.domain.calendar import MarketCalendar

CALENDAR = MarketCalendar(day for day, _ in seeds.HOLIDAYS)

# Weekday F&O holidays: NSE/FAOP/71777 (12 Dec 2025) and NSE/FAOP/72262 (12 Jan 2026).
WEEKDAYS_2026 = {
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1),
    date(2026, 5, 28), date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
}  # fmt: skip
# NSE/FAOP/65588 (13 Dec 2024).
WEEKDAYS_2025 = {
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31), date(2025, 4, 10),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 21), date(2025, 10, 22),
    date(2025, 11, 5), date(2025, 12, 25),
}  # fmt: skip


def weekday_holidays(year: int) -> set[date]:
    return {day for day, _ in seeds.HOLIDAYS if day.year == year and day.weekday() < 5}


def test_the_weekday_holidays_match_the_circulars() -> None:
    assert weekday_holidays(2025) == WEEKDAYS_2025
    assert weekday_holidays(2026) == WEEKDAYS_2026


def test_diwali_2026_closes_tuesday_not_monday() -> None:
    assert CALENDAR.is_trading_day(date(2026, 11, 9))
    assert not CALENDAR.is_trading_day(date(2026, 11, 10))
    assert CALENDAR.next_trading_day(date(2026, 11, 6)) == date(2026, 11, 9)


def test_only_published_years_are_listed() -> None:
    """A guessed date would close the market on a day it may be open."""
    assert {day.year for day, _ in seeds.HOLIDAYS} == {2025, 2026}


def test_each_day_appears_once_with_a_reason() -> None:
    days = [day for day, _ in seeds.HOLIDAYS]
    assert len(days) == len(set(days)) == len(seeds.HOLIDAYS)
    assert all(name.strip() for _, name in seeds.HOLIDAYS)
    assert days == sorted(days)
