"""SimulatedMarket: made-up prices for running everything locally, with no broker at all.

It reports is_real = False, so live orders refuse to run on it.
"""

import asyncio
import math
import random
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal

from coolcandle.domain.calendar import MARKET_OPEN, ist_datetime, to_ist
from coolcandle.domain.models import Candle, Instrument, InstrumentKind, OptionType, Tick
from coolcandle.ports.clock import Clock

TICK = Decimal("0.05")
UNDERLYINGS = {
    "NIFTY": ("nse_cm|Nifty 50", "nse_fo", 24500.0, 50, 65, 1),  # weekly expiry on Tuesday
    "SENSEX": ("bse_cm|SENSEX", "bse_fo", 80000.0, 100, 20, 3),  # weekly expiry on Thursday
}
STRIKES_EACH_SIDE = 12


def _price(value: float) -> Decimal:
    return (Decimal(str(max(value, 0.05))) / TICK).to_integral_value() * TICK


def option_premium(spot: float, strike: float, side: OptionType, step: int) -> float:
    intrinsic = max(spot - strike, 0.0) if side is OptionType.CE else max(strike - spot, 0.0)
    time_value = 3.0 * step * math.exp(-(((spot - strike) / (4 * step)) ** 2))
    return intrinsic + time_value


class SimulatedMarket:
    is_real = False

    def __init__(self, clock: Clock, seed: int = 19, speed: float = 1.0) -> None:
        self.clock = clock
        self.speed = speed
        self.random = random.Random(seed)
        self.spots = {name: spec[2] for name, spec in UNDERLYINGS.items()}
        self._instruments: dict[str, Instrument] = {}
        self._subscribed: set[str] = set()
        self._volume = 0

    def _next_expiry(self, today: date, weekday: int) -> date:
        return today + timedelta(days=(weekday - today.weekday()) % 7)

    async def instruments(self) -> list[Instrument]:
        today = to_ist(self.clock.now()).date()
        found: list[Instrument] = []
        for name, (index_key, segment, spot, step, lot, weekday) in UNDERLYINGS.items():
            found.append(
                Instrument(
                    key=index_key,
                    exchange_segment=index_key.split("|")[0],
                    trading_symbol=name,
                    kind=InstrumentKind.INDEX,
                    underlying=name,
                )
            )
            expiry = self._next_expiry(today, weekday)
            centre = round(spot / step) * step
            for offset in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1):
                strike = centre + offset * step
                for side in OptionType:
                    found.append(
                        Instrument(
                            key=f"{segment}|{name}{strike}{side}",
                            exchange_segment=segment,
                            trading_symbol=f"{name}{expiry:%y%b}{strike}{side}".upper(),
                            kind=InstrumentKind.OPTION,
                            underlying=name,
                            expiry=expiry,
                            strike=Decimal(strike),
                            option_type=side,
                            lot_size=lot,
                            tick_size=TICK,
                            freeze_qty=lot * 27,
                        )
                    )
        self._instruments = {i.key: i for i in found}
        return found

    async def subscribe(self, keys: list[str]) -> None:
        self._subscribed |= set(keys)

    def _option_tick(self, instrument: Instrument, now: datetime) -> Tick:
        assert instrument.strike is not None and instrument.option_type is not None
        step = next(s[3] for n, s in UNDERLYINGS.items() if n == instrument.underlying)
        premium = option_premium(
            self.spots[instrument.underlying],
            float(instrument.strike),
            instrument.option_type,
            step,
        )
        premium *= 1 + self.random.uniform(-0.002, 0.002)
        ltp = _price(premium)
        return Tick(
            instrument_key=instrument.key,
            ts=now,
            received_at=now,
            ltp=ltp,
            bid=max(ltp - TICK, TICK),
            ask=ltp + TICK,
            volume_today=self._volume,
            oi=100_000,
        )

    def step(self) -> list[Tick]:
        """One beat of the market: every index moves, and every option follows it."""
        now = self.clock.now()
        self._volume += self.random.randint(100, 2000)
        ticks: list[Tick] = []
        for name, spec in UNDERLYINGS.items():
            self.spots[name] += self.random.gauss(0, spec[2] * 0.00004)
            ticks.append(
                Tick(
                    instrument_key=spec[0],
                    ts=now,
                    received_at=now,
                    ltp=_price(self.spots[name]),
                )
            )
        for instrument in self._instruments.values():
            if instrument.kind is InstrumentKind.OPTION:
                ticks.append(self._option_tick(instrument, now))
        return ticks

    async def ticks(self) -> AsyncIterator[Tick]:
        while True:
            for tick in self.step():
                yield tick
            await asyncio.sleep(0.5 / self.speed)

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        """A seeded walk back from the current price, so the chart is not empty on load."""
        spec = next((s for s in UNDERLYINGS.values() if s[0] == key), None)
        if spec is None or interval not in ("1m", "5m"):
            return []
        minutes = 1 if interval == "1m" else 5
        now = self.clock.now()
        opens = ist_datetime(to_ist(now).date(), MARKET_OPEN)
        walk = random.Random(f"{key}{interval}{start}")
        count = max(int((now - opens).total_seconds() // (minutes * 60)), 0)
        close = spec[2]
        candles: list[Candle] = []
        for i in range(count):
            open_ = close
            close = open_ + walk.gauss(0, spec[2] * 0.00035 * math.sqrt(minutes))
            wick = abs(walk.gauss(0, spec[2] * 0.00015))
            candles.append(
                Candle(
                    instrument_key=key,
                    interval=interval,
                    start=opens + timedelta(minutes=i * minutes),
                    open=_price(open_),
                    high=_price(max(open_, close) + wick),
                    low=_price(min(open_, close) - wick),
                    close=_price(close),
                )
            )
        return candles
