"""Support and resistance breakout: a closed bar beyond a level from the prior sessions."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from coolcandle.domain.models import Candle
from coolcandle.domain.setups.base import Direction, SetupHit
from coolcandle.domain.setups.levels import find_levels


@dataclass(frozen=True)
class LevelBreakout:
    min_break_points: Decimal = Decimal("5")
    stop_buffer_points: Decimal = Decimal("10")
    min_touches: int = 2
    bar_minutes: int = 5
    name: str = "level_breakout"
    version: str = "1"

    def check(self, underlying: str, today: list[Candle], history: list[Candle]) -> SetupHit | None:
        if len(today) < 2:
            return None
        previous, bar = today[-2], today[-1]
        for level in find_levels(history):
            if level.touches < self.min_touches:
                continue
            crossed_up = (
                previous.close <= level.price and bar.close >= level.price + self.min_break_points
            )
            crossed_down = (
                previous.close >= level.price and bar.close <= level.price - self.min_break_points
            )
            if not (crossed_up or crossed_down):
                continue
            direction = Direction.BULLISH if crossed_up else Direction.BEARISH
            buffer = self.stop_buffer_points
            stop = level.price - buffer if crossed_up else level.price + buffer
            return SetupHit(
                setup=self.name,
                version=self.version,
                underlying=underlying,
                direction=direction,
                level=level.price,
                close=bar.close,
                index_stop=stop,
                at=bar.start + timedelta(minutes=self.bar_minutes),
                reason=f"Closed {bar.close} through {level.price:.2f} ({level.touches} touches)",
                features={"touches": level.touches, "break_points": str(bar.close - level.price)},
            )
        return None
