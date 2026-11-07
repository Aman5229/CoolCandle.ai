"""Exit planning: where the stop and target go, set before the entry and refused if senseless."""

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from coolcandle.domain.models import Candle, Frozen


class ExitPlanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StopRule(StrEnum):
    PRICE = "PRICE"
    PCT = "PCT"
    ATR = "ATR"


class ExitPlan(Frozen):
    entry: Decimal
    stop: Decimal
    target: Decimal
    risk: Decimal
    reward_risk: Decimal


def floor_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def ceil_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def stop_limit_price(trigger: Decimal, tick: Decimal, pct: Decimal) -> Decimal:
    """The limit under an SL trigger: `pct` below it, at least one tick, never under one tick."""
    limit = floor_tick(trigger * (100 - pct) / 100, tick)
    return max(min(limit, trigger - tick), tick)


def average_true_range(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    """Mean true range of the last `period` candles, or of all of them when there are fewer."""
    if len(candles) < 2:
        return None
    ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    recent = ranges[-period:]
    return sum(recent, Decimal(0)) / len(recent)


def plan_exit(
    entry: Decimal,
    rule: StopRule,
    value: Decimal,
    target_r: Decimal,
    tick: Decimal,
    *,
    candles: Sequence[Candle] = (),
    min_stop_pct: Decimal = Decimal("0.5"),
) -> ExitPlan:
    """A long option trade's stop and target. The stop rounds away from the entry."""
    if rule is StopRule.PRICE:
        raw_stop = value
    elif rule is StopRule.PCT:
        raw_stop = entry * (100 - value) / 100
    else:
        atr = average_true_range(candles)
        if atr is None:
            raise ExitPlanError("NO_CANDLES", "An ATR stop needs at least two candles")
        raw_stop = entry - value * atr

    stop = floor_tick(raw_stop, tick)
    if stop <= 0 or stop >= entry:
        raise ExitPlanError("STOP_SIDE", "The stop must sit below the entry and above zero")
    risk = entry - stop
    if risk / entry * 100 < min_stop_pct:
        raise ExitPlanError("STOP_TOO_TIGHT", "That stop is inside normal spread and wiggle")
    if target_r <= 0:
        raise ExitPlanError("TARGET_SIDE", "The target must sit above the entry")
    target = ceil_tick(entry + target_r * risk, tick)
    return ExitPlan(
        entry=entry, stop=stop, target=target, risk=risk, reward_risk=(target - entry) / risk
    )
