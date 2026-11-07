"""Trading days and session times in IST. Pure: the caller passes the time in."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DAY_RESET = time(9, 0)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def to_ist(moment: datetime) -> datetime:
    return moment.astimezone(IST)


def ist_datetime(day: date, at: time) -> datetime:
    """The UTC instant of a wall-clock IST time."""
    return datetime.combine(day, at, IST).astimezone(UTC)


class MarketCalendar:
    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self.holidays = frozenset(holidays)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def next_trading_day(self, day: date) -> date:
        day += timedelta(days=1)
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, day: date) -> date:
        day -= timedelta(days=1)
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def trading_date(self, now: datetime) -> date:
        """The session a moment belongs to. It rolls over at 09:00 IST on a trading day, so a
        lock from the last session holds through nights, weekends and holidays."""
        local = to_ist(now)
        if self.is_trading_day(local.date()) and local.time() >= DAY_RESET:
            return local.date()
        return self.previous_trading_day(local.date())

    def is_market_open(self, now: datetime) -> bool:
        local = to_ist(now)
        return self.is_trading_day(local.date()) and MARKET_OPEN <= local.time() < MARKET_CLOSE

    def lock_ends_at(self, trading_date: date) -> datetime:
        return ist_datetime(self.next_trading_day(trading_date), DAY_RESET)
