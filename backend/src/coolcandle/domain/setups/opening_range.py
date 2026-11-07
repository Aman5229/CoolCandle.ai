"""Opening-range breakout: the first closed bar beyond the first 15 minutes' high or low."""

from dataclasses import dataclass
from datetime import timedelta

from coolcandle.domain.models import Candle
from coolcandle.domain.setups.base import Direction, SetupHit


@dataclass(frozen=True)
class OpeningRangeBreakout:
    range_minutes: int = 15
    bar_minutes: int = 5
    name: str = "opening_range"
    version: str = "1"

    def check(self, underlying: str, today: list[Candle], history: list[Candle]) -> SetupHit | None:
        bars = self.range_minutes // self.bar_minutes
        if len(today) <= bars:
            return None
        opening = today[:bars]
        high = max(c.high for c in opening)
        low = min(c.low for c in opening)
        middle = (high + low) / 2
        # Only the first close beyond the range counts: later bars never reset it.
        for bar in today[bars:]:
            direction = None
            if bar.close > high:
                direction, level = Direction.BULLISH, high
            elif bar.close < low:
                direction, level = Direction.BEARISH, low
            if direction is None:
                continue
            if bar is not today[-1]:
                return None  # it fired on an earlier bar, so this one is late
            closes_at = bar.start + timedelta(minutes=self.bar_minutes)
            return SetupHit(
                setup=self.name,
                version=self.version,
                underlying=underlying,
                direction=direction,
                level=level,
                close=bar.close,
                index_stop=middle,
                at=closes_at,
                reason=f"Closed {bar.close} beyond the opening range {low} to {high}",
                features={
                    "range_high": str(high),
                    "range_low": str(low),
                    "range_points": str(high - low),
                    "break_points": str(abs(bar.close - level)),
                },
            )
        return None
