"""Builds 1-minute and 5-minute candles from ticks. Closed bars are what setups read."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from coolcandle.domain.calendar import MARKET_OPEN, ist_datetime, to_ist
from coolcandle.domain.models import Candle, Tick

INTERVALS = {"1m": 1, "5m": 5}


@dataclass
class _Forming:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    first_volume: int | None
    last_volume: int | None
    oi: int | None


def bucket_start(moment: datetime, minutes: int) -> datetime:
    """Buckets count from the 09:15 open, so 5-minute bars line up with the session."""
    opens = ist_datetime(to_ist(moment).date(), MARKET_OPEN)
    elapsed = int((moment - opens).total_seconds() // (minutes * 60))
    return opens + timedelta(minutes=elapsed * minutes)


class CandleBuilder:
    def __init__(self) -> None:
        self._forming: dict[tuple[str, str], _Forming] = {}
        self._closed: dict[tuple[str, str], list[Candle]] = defaultdict(list)
        self._history: dict[tuple[str, str], list[Candle]] = {}

    def set_history(self, key: str, interval: str, candles: list[Candle]) -> None:
        self._history[(key, interval)] = candles

    def history(self, key: str, interval: str) -> list[Candle]:
        """Earlier candles: backfilled sessions, and whatever came before this process."""
        return self._history.get((key, interval), [])

    def update(self, tick: Tick) -> list[Candle]:
        """Fold a tick in. Returns the candles this tick closed."""
        closed: list[Candle] = []
        for interval, minutes in INTERVALS.items():
            slot = (tick.instrument_key, interval)
            start = bucket_start(tick.ts, minutes)
            forming = self._forming.get(slot)
            if forming is not None and forming.start != start:
                candle = self._finish(tick.instrument_key, interval, forming)
                self._closed[slot].append(candle)
                closed.append(candle)
                forming = None
            if forming is None:
                self._forming[slot] = _Forming(
                    start=start,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    first_volume=tick.volume_today,
                    last_volume=tick.volume_today,
                    oi=tick.oi,
                )
                continue
            forming.high = max(forming.high, tick.ltp)
            forming.low = min(forming.low, tick.ltp)
            forming.close = tick.ltp
            forming.last_volume = tick.volume_today or forming.last_volume
            forming.oi = tick.oi or forming.oi
        return closed

    def _finish(self, key: str, interval: str, forming: _Forming) -> Candle:
        volume = 0
        if forming.first_volume is not None and forming.last_volume is not None:
            volume = max(forming.last_volume - forming.first_volume, 0)
        return Candle(
            instrument_key=key,
            interval=interval,
            start=forming.start,
            open=forming.open,
            high=forming.high,
            low=forming.low,
            close=forming.close,
            volume=volume,
            oi=forming.oi,
        )

    def closed(self, key: str, interval: str) -> list[Candle]:
        return list(self._closed[(key, interval)])

    def with_forming(self, key: str, interval: str) -> list[Candle]:
        candles = self.closed(key, interval)
        forming = self._forming.get((key, interval))
        if forming is not None:
            candles.append(self._finish(key, interval, forming))
        return candles

    def reset(self) -> None:
        self._forming.clear()
        self._closed.clear()
