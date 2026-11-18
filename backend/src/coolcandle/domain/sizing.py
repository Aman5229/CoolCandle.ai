"""How many lots a ticket can take: the smallest of every limit, and which limit decided it."""

from decimal import Decimal

from coolcandle.domain.costs import CostRates, round_trip
from coolcandle.domain.exits import floor_tick, stop_limit_price
from coolcandle.domain.models import DaySnapshot, Frozen
from coolcandle.domain.plan import DailyPlan


class SizeResult(Frozen):
    lots: int
    limited_by: str
    limits: dict[str, int]


def worst_case_loss(
    entry: Decimal, stop_limit: Decimal, qty: int, slippage_points: Decimal, rates: CostRates
) -> Decimal:
    """Loss if the stop fills at its limit, less the buffer's slippage, with full charges."""
    return (
        (entry - stop_limit) * qty
        + round_trip(entry, stop_limit, qty, rates)
        + slippage_points * qty
    )


def stop_range(
    entry: Decimal,
    qty: int,
    tick: Decimal,
    budget: Decimal,
    rates: CostRates,
    *,
    stop_limit_pct: Decimal,
    min_stop_pct: Decimal,
    slippage_points: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """(lowest, highest) stop trigger the guard allows for `qty` bought at `entry`, worked out
    exactly as the guard checks it. The highest keeps the stop `min_stop_pct` below the entry;
    the lowest is the furthest a stop can go before its worst case (the fall to the stop
    order's limit, the slippage buffer and the charges) costs more than `budget`. None when even
    the tightest stop costs too much."""
    highest = floor_tick(entry * (100 - min_stop_pct) / 100, tick)
    if qty < 1 or highest < tick:
        return None

    def worst(trigger: Decimal) -> Decimal:
        limit = stop_limit_price(trigger, tick, stop_limit_pct)
        return worst_case_loss(entry, limit, qty, slippage_points, rates)

    if worst(highest) > budget:
        return None
    low, high = 1, int(highest / tick)  # in ticks; the worst case falls as the stop rises
    while low < high:
        middle = (low + high) // 2
        if worst(tick * middle) <= budget:
            high = middle
        else:
            low = middle + 1
    return tick * low, highest


def _lots_within(
    budget: Decimal,
    entry: Decimal,
    stop_limit: Decimal,
    lot_size: int,
    slippage_points: Decimal,
    rates: CostRates,
) -> int:
    if budget <= 0:
        return 0
    per_lot = (entry - stop_limit + slippage_points) * lot_size
    if per_lot <= 0:
        return 0
    lots = int(budget / per_lot)
    # Charges are not linear in lots, so step down until the exact worst case fits.
    while lots > 0:
        qty = lots * lot_size
        if worst_case_loss(entry, stop_limit, qty, slippage_points, rates) <= budget:
            return lots
        lots -= 1
    return 0


def max_lots(
    plan: DailyPlan,
    snapshot: DaySnapshot,
    entry: Decimal,
    stop_limit: Decimal,
    lot_size: int,
    rates: CostRates,
    *,
    slippage_points: Decimal,
    cash: Decimal | None = None,
    freeze_qty: int | None = None,
) -> SizeResult:
    day_budget = plan.loss_limit + snapshot.net_pnl
    limits = {
        "max_loss_per_trade": _lots_within(
            plan.max_loss_per_trade, entry, stop_limit, lot_size, slippage_points, rates
        ),
        "day_budget": _lots_within(day_budget, entry, stop_limit, lot_size, slippage_points, rates),
    }
    if cash is not None:
        limits["cash"] = int(cash / (entry * lot_size)) if entry > 0 else 0
    if freeze_qty is not None:
        limits["freeze_qty"] = freeze_qty // lot_size
    limited_by = min(limits, key=lambda name: limits[name])
    return SizeResult(lots=limits[limited_by], limited_by=limited_by, limits=limits)
