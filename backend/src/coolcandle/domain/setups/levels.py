"""Support and resistance from swing points on closed candles."""

from collections.abc import Sequence
from decimal import Decimal

from coolcandle.domain.models import Candle, Frozen


class Level(Frozen):
    price: Decimal
    touches: int


def swing_points(candles: Sequence[Candle], width: int) -> tuple[list[Decimal], list[Decimal]]:
    """Swing highs and lows: a bar higher (or lower) than `width` bars on each side."""
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    for i in range(width, len(candles) - width):
        around = [*candles[i - width : i], *candles[i + 1 : i + width + 1]]
        if all(candles[i].high > other.high for other in around):
            highs.append(candles[i].high)
        if all(candles[i].low < other.low for other in around):
            lows.append(candles[i].low)
    return highs, lows


def cluster(prices: Sequence[Decimal], tolerance: Decimal) -> list[Level]:
    """Merge prices within `tolerance` of each other into levels, strongest first."""
    groups: list[list[Decimal]] = []
    for price in sorted(prices):
        if groups and price - groups[-1][-1] <= tolerance:
            groups[-1].append(price)
        else:
            groups.append([price])
    levels = [Level(price=sum(g, Decimal(0)) / len(g), touches=len(g)) for g in groups]
    return sorted(levels, key=lambda level: level.touches, reverse=True)


def find_levels(
    candles: Sequence[Candle], *, width: int = 3, tolerance: Decimal = Decimal("10")
) -> list[Level]:
    highs, lows = swing_points(candles, width)
    return cluster([*highs, *lows], tolerance)
