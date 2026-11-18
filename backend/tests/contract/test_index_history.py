"""Skill Test's adapters: the index history on disk, the candle replay, and Kotak's history fetch
against a fake client. Nothing here calls Kotak."""

import asyncio
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coolcandle.adapters.brokers.kotak_neo.history import HistoryUnavailable, history
from coolcandle.adapters.clock import ManualClock
from coolcandle.adapters.market.history.replay import (
    TICKS_PER_MINUTE,
    CandleReplay,
    candle_path,
    walk,
)
from coolcandle.adapters.market.history.store import IndexHistory
from coolcandle.domain.calendar import ist_datetime, to_ist
from coolcandle.domain.models import Candle
from coolcandle.domain.skill.charts import fold
from coolcandle.domain.skill.options import index_instrument

DAY = date(2025, 11, 10)
MINUTE = timedelta(minutes=1)


def minute(day: date, at: time, o: str, h: str, low: str, c: str) -> Candle:
    return Candle(
        instrument_key="skill|NIFTY",
        interval="1m",
        start=ist_datetime(day, at),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def session(day: date, count: int) -> list[Candle]:
    """`count` minutes from 09:15, wandering up and down."""
    out = []
    for i in range(count):
        base = Decimal(25000 + (i % 7) * 3 - (i % 5) * 2)
        at = to_ist(ist_datetime(day, time(9, 15)) + i * MINUTE).time()
        out.append(minute(day, at, str(base), str(base + 4), str(base - 3), str(base + 1)))
    return out


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


# --- the store ------------------------------------------------------------------------------


def test_minutes_are_saved_by_month_and_read_back_exactly(tmp_path: Path) -> None:
    store = IndexHistory(tmp_path)
    october = session(date(2025, 10, 31), 3)
    november = session(DAY, 5)
    store.save("NIFTY", [*october, *november])
    assert sorted(p.name for p in (tmp_path / "skill/history/NIFTY/1m").iterdir()) == [
        "2025-10.parquet",
        "2025-11.parquet",
    ]
    assert store.days("NIFTY") == [date(2025, 10, 31), DAY]
    opens = ist_datetime(DAY, time(9, 15))
    assert store.window("NIFTY", opens, opens + 3 * MINUTE) == november[:3]
    assert store.window("NIFTY", opens - timedelta(days=30), opens) == october


def test_saving_again_replaces_a_minute_and_keeps_the_rest(tmp_path: Path) -> None:
    store = IndexHistory(tmp_path)
    store.save("NIFTY", session(DAY, 5))
    fixed = minute(DAY, time(9, 16), "1", "2", "0.5", "1.5")
    store.save("NIFTY", [fixed])
    opens = ist_datetime(DAY, time(9, 15))
    stored = store.window("NIFTY", opens, opens + timedelta(hours=1))
    assert len(stored) == 5
    assert stored[1] == fixed


@pytest.mark.parametrize("interval", ["5m", "15m", "1h", "1d"])
def test_the_store_folds_timeframes_exactly_as_the_domain_does(
    tmp_path: Path, interval: str
) -> None:
    store = IndexHistory(tmp_path)
    days = [session(DAY - timedelta(days=d), 130) for d in (0, 1, 3)]
    store.save("NIFTY", [c for day in days for c in day])
    start, end = ist_datetime(DAY - timedelta(days=5), time()), ist_datetime(DAY, time(23))
    expected = fold(store.window("NIFTY", start, end), interval)
    assert store.bars("NIFTY", interval, start, end) == expected


def test_an_index_without_files_has_no_days(tmp_path: Path) -> None:
    store = IndexHistory(tmp_path)
    assert store.days("SENSEX") == []
    opens = ist_datetime(DAY, time(9, 15))
    assert store.bars("SENSEX", "1h", opens, opens + timedelta(days=1)) == []


# --- the replay -----------------------------------------------------------------------------


def test_a_candle_is_walked_to_its_high_and_low_exactly() -> None:
    rising = minute(DAY, time(9, 15), "100", "110", "95", "105")
    assert candle_path(rising) == [100, 110, 95, 105]
    assert candle_path(rising, "low") == [100, 95, 110, 105]
    falling = minute(DAY, time(9, 15), "105", "110", "95", "100")
    assert candle_path(falling) == [105, 95, 110, 100]


async def test_a_minute_plays_the_same_prices_at_every_speed() -> None:
    """The speed only changes the wait: a stop must fill at the same price at 1x and 60x."""
    day = session(DAY, 2)
    played: dict[int, list[Decimal]] = {}
    waits: dict[int, list[float]] = {1: [], 60: []}
    for speed in (1, 60):

        async def record(seconds: float, speed: int = speed) -> None:
            waits[speed].append(seconds)

        replay = CandleReplay(
            index_instrument("NIFTY"), day, day[0].start, ManualClock(day[0].start),
            speed=speed, sleep=record,
        )  # fmt: skip
        played[speed] = [tick.ltp async for tick in replay.ticks()]
    assert played[1] == played[60]
    assert len(played[1]) == 2 * TICKS_PER_MINUTE
    assert set(waits[1]) == {0.5}  # half a second of market time, played in real time
    assert all(abs(w - 0.5 / 60) < 1e-9 for w in waits[60])


@pytest.mark.parametrize("count", [4, 12, 48, 240])
def test_the_walk_reaches_every_corner_and_never_leaves_the_range(count: int) -> None:
    candle = minute(DAY, time(9, 20), "25010.35", "25042.10", "24987.55", "25030.00")
    corners = candle_path(candle)
    prices = walk(candle, corners, count)
    assert len(prices) == count
    assert prices[0] == candle.open and prices[-1] == candle.close
    assert candle.high in prices and candle.low in prices
    assert all(candle.low <= p <= candle.high for p in prices)
    assert prices.index(candle.high) < prices.index(candle.low)  # a rising minute: high first
    assert all(p == p.quantize(Decimal("0.01")) for p in prices)
    assert walk(candle, corners, count) == prices  # the same minute always plays the same way


def test_the_walk_wanders_instead_of_moving_in_straight_steps() -> None:
    candle = minute(DAY, time(9, 20), "25000", "25040", "24990", "25030")
    prices = walk(candle, candle_path(candle), 240)
    moves = {b - a for a, b in zip(prices, prices[1:], strict=False)}
    assert len(moves) > 10  # not a handful of equal steps
    assert any(b < a for a, b in zip(prices[:80], prices[1:80], strict=False))  # dips on a rise


async def test_the_replay_plays_in_order_and_never_shows_the_future() -> None:
    day = session(DAY, 6)
    clock = ManualClock(day[0].start)
    replay = CandleReplay(
        index_instrument("NIFTY"), day, day[2].start, clock, per_minute=4, sleep=no_sleep
    )
    assert replay.played() == day[:2]  # the minutes before the start count as played
    seen = 0
    async for tick in replay.ticks():
        seen += 1
        assert clock.now() == tick.ts == replay.cursor
        assert all(c.start + MINUTE <= tick.ts for c in replay.played())
    assert seen == 4 * 4
    assert replay.finished and replay.played() == day
    five = await replay.history(replay.key, "5m", DAY, DAY)
    assert five == fold(day, "5m")


def test_a_speed_that_is_not_offered_is_refused() -> None:
    day = session(DAY, 1)
    replay = CandleReplay(index_instrument("NIFTY"), day, day[0].start, ManualClock(day[0].start))
    with pytest.raises(ValueError):
        replay.set_speed(7)


# --- Kotak's history ------------------------------------------------------------------------


class FakeKotak:
    """Answers `historical_data` like Kotak: rows of [time, o, h, l, c, volume], 429 at times."""

    def __init__(
        self,
        limited: int = 0,
        fail: bool = False,
        rows: list[Any] | None = None,
        silent: int = 0,
    ) -> None:
        self.calls: list[dict[str, str]] = []
        self.limited = limited
        self.fail = fail
        self.rows = rows
        self.silent = silent  # how many requests time out before Kotak answers

    def historical_data(self, **kwargs: str) -> Any:
        self.calls.append(kwargs)
        if self.silent:
            self.silent -= 1
            raise TimeoutError("Request timeout after 30 seconds")
        if self.limited:
            self.limited -= 1
            return {"code": 429}
        if self.fail:
            return {"Error": "Something went wrong"}
        if self.rows is not None:
            return {"status": "success", "data": {"candles": self.rows}}
        start = date.fromisoformat(kwargs["from_date"])
        rows = [
            [
                f"{(start + timedelta(days=d)).isoformat()}T09:15:00+05:30",
                25000,
                25010,
                24990,
                25005,
                0,
            ]
            for d in range(3)
        ]
        return {"status": "SUCCESS", "data": {"candles": rows}}


async def test_history_is_fetched_in_kotaks_chunks_and_paced() -> None:
    client = FakeKotak()
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    candles = await history(
        client, "nse_cm|Nifty 50", "1m", date(2025, 1, 1), date(2025, 3, 1),
        candle_key="skill|NIFTY", sleep=record,
    )  # fmt: skip
    assert [c["from_date"] for c in client.calls] == ["2025-01-01", "2025-01-29", "2025-02-26"]
    assert {c["interval"] for c in client.calls} == {"1min"}
    assert waits == [2.0, 2.0, 2.0]
    assert candles[0].instrument_key == "skill|NIFTY"
    assert candles[0].close == Decimal("25005")
    assert [c.start for c in candles] == sorted(c.start for c in candles)


async def test_a_rate_limit_is_waited_out_with_backoff() -> None:
    client = FakeKotak(limited=2)
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    candles = await history(client, "nse_cm|Nifty 50", "1m", DAY, DAY, sleep=record)
    assert len(client.calls) == 3
    assert waits == [4.0, 8.0, 2.0]  # two backoffs, then the normal pause
    assert candles


async def test_a_refusal_gives_no_candles_instead_of_failing() -> None:
    candles = await history(
        FakeKotak(fail=True), "nse_cm|India VIX", "1m", DAY, DAY, sleep=no_sleep
    )
    assert candles == []


async def test_a_minute_with_its_open_outside_its_range_is_widened_not_lost() -> None:
    rows = [
        ["2025-11-10T09:15:00+05:30", 25120.5, 25115, 25100, 25110.25, 0],  # open above the high
        ["2025-11-10T09:16:00+05:30", 25110, 25112, 25105, 25099.5, 0],  # close below the low
        ["2025-11-10T09:17:00+05:30", 25100, 25104, 25098, 25101, 0],
    ]
    client = FakeKotak(rows=rows)
    candles = await history(client, "nse_cm|Nifty 50", "1m", DAY, DAY, sleep=no_sleep)
    assert len(candles) == 3
    assert (candles[0].high, candles[0].low) == (Decimal("25120.5"), Decimal("25100"))
    assert (candles[1].high, candles[1].low) == (Decimal("25112"), Decimal("25099.5"))
    assert candles[2].high == Decimal("25104")


async def test_a_request_that_times_out_is_tried_again() -> None:
    client = FakeKotak(silent=2)
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    candles = await history(client, "nse_cm|Nifty 50", "1m", DAY, DAY, sleep=record)
    assert len(client.calls) == 3
    assert waits == [4.0, 8.0, 2.0]
    assert candles


async def test_when_kotak_never_answers_it_says_so_instead_of_leaving_a_gap() -> None:
    with pytest.raises(HistoryUnavailable, match="did not answer"):
        await history(FakeKotak(silent=99), "nse_cm|Nifty 50", "1m", DAY, DAY, sleep=no_sleep)
