"""CandleReplay: one past trading day of 1-minute candles, played through its own clock.

Each minute becomes a walk of ticks through its corners: open, high, low, close for a rising
minute and open, low, high, close for a falling one. Between corners the price wanders as a real
one does, inside the minute's range, and every corner is reached exactly. A minute is always the
same 120 prices, one each half second of market time, whatever the speed: the speed changes only
how fast they play, so a stop fills at the same price at 1x and at 60x. The wandering is seeded
by the minute's start, so a minute always plays the same way, a restart included. When an open
trade's stop and target both sit inside one minute, its stop side is walked first: the
conservative fill. Nothing after the cursor ever leaves this class."""

import asyncio
import math
import random
from bisect import bisect_left
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

from coolcandle.adapters.clock import ManualClock
from coolcandle.domain.models import Candle, Instrument, Tick
from coolcandle.domain.skill.charts import fold
from coolcandle.domain.skill.models import REPLAY_SPEEDS, StopSide

MINUTE = timedelta(minutes=1)
TICKS_PER_MINUTE = 120  # one each half second of market time, at every speed
CORNERS = 4


def candle_path(candle: Candle, stop_side: StopSide | None = None) -> list[Decimal]:
    """The four corners a candle is walked through. `stop_side` says which extreme an open
    trade's stop is on, when its stop and target are both inside this candle."""
    o, h, low, c = candle.open, candle.high, candle.low, candle.close
    if stop_side == "low":
        return [o, low, h, c]
    if stop_side == "high":
        return [o, h, low, c]
    return [o, h, low, c] if c >= o else [o, low, h, c]


def walk(candle: Candle, corners: list[Decimal], count: int) -> list[Decimal]:
    """`count` prices from the open to the close through the corners. Between two corners the
    price takes a random walk pulled back onto its path (a Brownian bridge), kept inside the
    minute's range. All of it is whole paise, so it is exact, and seeded by the minute's start."""
    rng = random.Random(int(candle.start.timestamp()))
    cents = [int(c * 100) for c in corners]
    low, high = int(candle.low * 100), int(candle.high * 100)
    legs = list(zip(cents, cents[1:], strict=False))
    spare = count - CORNERS
    lengths = [abs(b - a) + 1 for a, b in legs]
    shares = [spare * length // sum(lengths) for length in lengths]
    shares[shares.index(max(shares))] += spare - sum(shares)  # give the rounding to the longest
    amplitude = max(1, (high - low) // 10)
    path = [cents[0]]
    for (a, b), extra in zip(legs, shares, strict=False):
        steps = extra + 1
        drift = [rng.randint(-amplitude, amplitude) for _ in range(steps)]
        walked = list(_running(drift))
        for i in range(1, steps):
            bridged = walked[i - 1] - walked[-1] * i // steps
            point = a + (b - a) * i // steps + bridged // max(1, math.isqrt(steps) // 2)
            path.append(min(max(point, low), high))
        path.append(b)
    return [Decimal(point) / 100 for point in path]


def _running(steps: list[int]) -> list[int]:
    total, out = 0, []
    for step in steps:
        total += step
        out.append(total)
    return out


class CandleReplay:
    is_real = False

    def __init__(
        self,
        instrument: Instrument,
        candles: list[Candle],
        start: datetime,
        clock: ManualClock,
        *,
        speed: int = 1,
        paused: bool = False,
        per_minute: int | None = None,
        stop_side: Callable[[Candle], StopSide | None] = lambda _: None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.instrument = instrument
        self.key = instrument.key
        self._candles = candles
        self._index = bisect_left([c.start for c in candles], start)
        self.cursor = start  # everything before this has been played
        self.clock = clock
        self.speed = speed
        self.per_minute = per_minute or TICKS_PER_MINUTE  # tests use 4: the corners alone
        self._stop_side = stop_side
        self._sleep = sleep
        self._running = asyncio.Event()
        if not paused:
            self._running.set()
        clock.set(start)

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    @property
    def finished(self) -> bool:
        return self._index >= len(self._candles)

    def play(self) -> None:
        self._running.set()

    def pause(self) -> None:
        self._running.clear()

    def set_speed(self, speed: int) -> None:
        if speed not in REPLAY_SPEEDS:
            raise ValueError(f"Speed must be one of {REPLAY_SPEEDS}")
        self.speed = speed

    async def instruments(self) -> list[Instrument]:
        return [self.instrument]

    async def subscribe(self, keys: list[str]) -> None:
        return None

    async def ticks(self) -> AsyncIterator[Tick]:
        while not self.finished:
            candle = self._candles[self._index]
            prices = walk(candle, candle_path(candle, self._stop_side(candle)), self.per_minute)
            gap = MINUTE / len(prices)
            for step, price in enumerate(prices):
                await self._running.wait()
                when = candle.start + gap * step
                self.clock.set(when)
                self.cursor = when
                yield Tick(
                    instrument_key=self.key,
                    ts=when,
                    received_at=when,
                    ltp=price,
                    bid=price,
                    ask=price,
                )
                await self._sleep(gap.total_seconds() / self.speed)
            self._index += 1
            self.cursor = candle.start + MINUTE
            self.clock.set(self.cursor)

    def played(self) -> list[Candle]:
        """The day's minutes that have finished, never one past the cursor."""
        return [c for c in self._candles if c.start + MINUTE <= self.cursor]

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        """Today's finished minutes in `interval`. A longer bar may still be forming, but it is
        built only from minutes already played."""
        return [c for c in fold(self.played(), interval) if start <= c.start.date() <= end]
