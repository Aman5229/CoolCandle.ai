"""How many lots a ticket can take: the smallest of every limit, and which limit decided it."""

from decimal import Decimal

from coolcandle.domain.costs import CostRates, round_trip
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
