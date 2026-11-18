"""Chart timeframes, folded from 1-minute candles. Bars start at the 09:15 open, so a 1-hour bar
runs 09:15 to 10:15 and a day is one bar, as on the live chart."""

from collections.abc import Sequence
from datetime import timedelta

from coolcandle.domain.calendar import MARKET_OPEN, ist_datetime, to_ist
from coolcandle.domain.models import Candle

INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 375}
# How far back each timeframe reaches, in calendar days: about two thousand bars at most.
REACH_DAYS = {"1m": 7, "5m": 30, "15m": 90, "1h": 365, "1d": 365}


def bar_start(candle: Candle, interval: str) -> Candle:
    opens = ist_datetime(to_ist(candle.start).date(), MARKET_OPEN)
    if interval == "1d":
        return candle.model_copy(update={"start": opens})
    size = timedelta(minutes=INTERVALS[interval])
    return candle.model_copy(update={"start": opens + ((candle.start - opens) // size) * size})


def fold(candles: Sequence[Candle], interval: str) -> list[Candle]:
    """1-minute candles, in order, into `interval` bars."""
    out: list[Candle] = []
    for candle in candles:
        placed = bar_start(candle, interval)
        last = out[-1] if out else None
        if last is not None and last.start == placed.start:
            out[-1] = last.model_copy(
                update={
                    "high": max(last.high, candle.high),
                    "low": min(last.low, candle.low),
                    "close": candle.close,
                    "volume": last.volume + candle.volume,
                }
            )
        else:
            out.append(placed.model_copy(update={"interval": interval}))
    return out
