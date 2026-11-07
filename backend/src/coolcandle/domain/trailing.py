"""Trailing-stop rules. Pure: each returns a tighter stop, or None to leave it alone."""

from collections.abc import Sequence
from decimal import Decimal

from coolcandle.domain.exits import average_true_range, floor_tick
from coolcandle.domain.models import Candle, Trade, TrailingRule, TrailKind


def _proposed(
    rule: TrailingRule, trade: Trade, candles: Sequence[Candle], ltp: Decimal
) -> Decimal | None:
    entry = trade.entry_avg
    if entry is None:
        return None
    if rule.kind is TrailKind.BREAKEVEN:
        return entry if ltp - entry >= rule.trigger_points else None
    if rule.kind is TrailKind.TARGET_TRAIL:
        if trade.target is None or ltp < trade.target:
            return None
        return ltp - rule.trail_points
    if rule.kind is TrailKind.STEP:
        steps = (ltp - entry) // rule.step_points
        if steps < 1:
            return None
        return entry + steps * rule.step_points - rule.trail_points
    if rule.kind is TrailKind.SWING_LOW:
        recent = candles[-rule.lookback :]
        if len(recent) < rule.lookback:
            return None
        return min(candle.low for candle in recent)
    atr = average_true_range(candles)
    return None if atr is None else ltp - rule.atr_multiple * atr


def next_stop(
    rule: TrailingRule, trade: Trade, candles: Sequence[Candle], ltp: Decimal
) -> Decimal | None:
    """The new stop trigger, only if it tightens the stop and stays under the market."""
    proposed = _proposed(rule, trade, candles, ltp)
    if proposed is None:
        return None
    stop = floor_tick(proposed, trade.tick_size)
    if stop <= trade.stop_trigger or stop >= ltp:
        return None
    return stop


def trails_past_target(rule: TrailingRule | None) -> bool:
    """A TARGET_TRAIL rule replaces the exit at the trade target."""
    return rule is not None and rule.kind is TrailKind.TARGET_TRAIL
