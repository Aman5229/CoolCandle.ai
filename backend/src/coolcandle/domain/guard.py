"""The guard: day state, lock triggers and the check before every order. Pure."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from coolcandle.domain.calendar import to_ist
from coolcandle.domain.costs import CostRates
from coolcandle.domain.events import MarketEvent, blocking_event, has_high_impact
from coolcandle.domain.exits import stop_limit_price
from coolcandle.domain.models import (
    Action,
    DaySnapshot,
    Decision,
    Frozen,
    Instrument,
    InstrumentKind,
    Intent,
    Mode,
    OrderRequest,
    Product,
    Reason,
    Side,
)
from coolcandle.domain.plan import DailyPlan, PlanStatus
from coolcandle.domain.sizing import worst_case_loss


class DayState(StrEnum):
    NO_PLAN = "NO_PLAN"
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    COOL_OFF = "COOL_OFF"
    ENTRIES_CLOSED = "ENTRIES_CLOSED"
    LOCKED = "LOCKED"


class LockReason(StrEnum):
    TARGET = "TARGET"
    LOSS_LIMIT = "LOSS_LIMIT"
    MAX_TRADES = "MAX_TRADES"
    SQUARE_OFF = "SQUARE_OFF"


@dataclass(frozen=True)
class GuardConfig:
    slippage_points: Decimal = Decimal("2")
    event_slippage_multiplier: Decimal = Decimal("2")
    band_pct: Decimal = Decimal("3")
    feed_max_age_s: float = 3.0
    live_order_cap: int = 30
    stop_limit_pct: Decimal = Decimal("2")
    min_stop_pct: Decimal = Decimal("0.5")


class DayStatus(Frozen):
    state: DayState
    reasons: tuple[Reason, ...] = ()
    lock_reason: LockReason | None = None


class OrderContext(Frozen):
    mode: Mode
    rates: CostRates
    instrument: Instrument | None = None
    ltp: Decimal | None = None
    lock_reason: LockReason | None = None
    has_open_trade: bool = False
    needs_attention: bool = False
    session_valid: bool = True
    market_open: bool = True
    live_orders_today: int = 0
    events: tuple[MarketEvent, ...] = ()
    current_stop: Decimal | None = None


def net_loss_used_pct(plan: DailyPlan, snapshot: DaySnapshot) -> Decimal:
    if snapshot.net_pnl >= 0:
        return Decimal(0)
    return -snapshot.net_pnl / plan.loss_limit * 100


def lock_trigger(plan: DailyPlan, snapshot: DaySnapshot) -> LockReason | None:
    """The limit that locks the day right now, if any. The loss limit always comes first."""
    if plan.status is not PlanStatus.LOCKED:
        return None
    net = snapshot.net_pnl
    if net <= -plan.loss_limit:
        return LockReason.LOSS_LIMIT
    if max(net, snapshot.peak_net_pnl) >= plan.profit_target:
        if not (plan.trail_at_target and snapshot.open_trade):
            return LockReason.TARGET
        floor = plan.profit_target * (100 - plan.target_giveback_pct) / 100
        if net < floor:
            return LockReason.TARGET
    if snapshot.trades_taken >= plan.max_trades and not snapshot.open_trade:
        return LockReason.MAX_TRADES
    local = to_ist(snapshot.now)
    if local.date() > plan.trading_date or local.time() >= plan.square_off:
        return LockReason.SQUARE_OFF
    return None


def cool_off_until(plan: DailyPlan, snapshot: DaySnapshot) -> timedelta | None:
    """Time left in the cool-off after a losing trade, if it is running."""
    if plan.cool_off_minutes == 0 or snapshot.last_loss_at is None:
        return None
    left = snapshot.last_loss_at + timedelta(minutes=plan.cool_off_minutes) - snapshot.now
    return left if left > timedelta(0) else None


def _closed_reasons(plan: DailyPlan, snapshot: DaySnapshot) -> tuple[Reason, ...]:
    now = to_ist(snapshot.now).time()
    reasons = []
    if now >= plan.entry_cutoff:
        reasons.append(Reason(code="ENTRY_CUTOFF", message="No new entries after the cutoff"))
    if snapshot.trades_taken >= plan.max_trades:
        reasons.append(Reason(code="MAX_TRADES", message="Today's trade count is used up"))
    if max(snapshot.net_pnl, snapshot.peak_net_pnl) >= plan.profit_target:
        reasons.append(
            Reason(code="TARGET_TRAILING", message="Day target reached: trailing the open trade")
        )
    return tuple(reasons)


def day_status(
    plan: DailyPlan | None,
    snapshot: DaySnapshot,
    lock_reason: LockReason | None = None,
    events: tuple[MarketEvent, ...] = (),
) -> DayStatus:
    if lock_reason is not None:
        return DayStatus(state=DayState.LOCKED, lock_reason=lock_reason)
    if plan is None or plan.status is not PlanStatus.LOCKED:
        return DayStatus(state=DayState.NO_PLAN)
    trigger = lock_trigger(plan, snapshot)
    if trigger is not None:
        return DayStatus(state=DayState.LOCKED, lock_reason=trigger)

    closed = _closed_reasons(plan, snapshot)
    if closed:
        return DayStatus(state=DayState.ENTRIES_CLOSED, reasons=closed)

    cooling = cool_off_until(plan, snapshot)
    if cooling is not None:
        minutes = int(cooling.total_seconds() // 60) + 1
        return DayStatus(
            state=DayState.COOL_OFF,
            reasons=(Reason(code="COOL_OFF", message=f"Cooling off for {minutes} more min"),),
        )

    warnings = []
    used = net_loss_used_pct(plan, snapshot)
    if used >= 80:
        warnings.append(Reason(code="LOSS_80", message="80% of today's loss limit is used"))
    elif used >= 50:
        warnings.append(Reason(code="LOSS_50", message="50% of today's loss limit is used"))
    if snapshot.trades_taken == plan.max_trades - 1:
        warnings.append(Reason(code="LAST_TRADE", message="One trade left today"))
    for event in events:
        if event.day == plan.trading_date:
            when = f" at {event.at:%H:%M}" if event.at else ""
            warnings.append(Reason(code="EVENT_DAY", message=f"{event.name}{when} today"))
    if warnings:
        return DayStatus(state=DayState.WARNING, reasons=tuple(warnings))
    return DayStatus(state=DayState.ACTIVE)


def slippage_points(
    plan: DailyPlan, events: tuple[MarketEvent, ...], config: GuardConfig
) -> Decimal:
    if has_high_impact(events, plan.trading_date):
        return config.slippage_points * config.event_slippage_multiplier
    return config.slippage_points


def _block(code: str, message: str) -> Reason:
    return Reason(code=code, message=message)


def _entry_failures(
    plan: DailyPlan | None,
    snapshot: DaySnapshot,
    request: OrderRequest,
    ctx: OrderContext,
    config: GuardConfig,
) -> list[Reason]:
    if ctx.lock_reason is not None:
        return [_block("DAY_LOCKED", "Trading is locked until the next trading day")]
    if plan is None or plan.status is not PlanStatus.LOCKED:
        return [_block("NO_PLAN", "Lock today's plan before placing orders")]

    status = day_status(plan, snapshot, None, ctx.events)
    if status.state is DayState.LOCKED:
        return [_block("DAY_LOCKED", "A day limit is reached")]

    failures: list[Reason] = []
    if status.state in (DayState.COOL_OFF, DayState.ENTRIES_CLOSED):
        failures.extend(status.reasons)
    if ctx.needs_attention:
        failures.append(_block("NEEDS_ATTENTION", "Resolve the flagged trade first"))
    if ctx.has_open_trade:
        failures.append(_block("OPEN_TRADE", "One open trade at a time"))

    if not ctx.market_open:
        failures.append(_block("MARKET_CLOSED", "The market is closed"))
    now_ist = to_ist(snapshot.now).time()
    if now_ist < plan.entry_start:
        failures.append(_block("ENTRY_WINDOW", f"No entries before {plan.entry_start:%H:%M}"))

    event = blocking_event(ctx.events, snapshot.now)
    if event is not None:
        failures.append(_block("EVENT_WINDOW", f"No new entries around {event.name}"))

    instrument = ctx.instrument
    if instrument is None:
        failures.append(_block("UNKNOWN_INSTRUMENT", "That instrument is not in today's list"))
        return failures
    if instrument.underlying not in plan.allowed_underlyings:
        failures.append(_block("UNDERLYING", f"{instrument.underlying} is not in today's plan"))
    if instrument.kind is not InstrumentKind.OPTION:
        failures.append(_block("INSTRUMENT_KIND", "Only index options can be traded"))
    if request.side is not Side.BUY:
        failures.append(_block("BUY_ONLY", "Only buying options is allowed"))
    if request.product is not Product.MIS:
        failures.append(_block("PRODUCT", "Only the intraday product is allowed"))

    entry, stop = request.price, request.stop
    if entry is None or stop is None:
        failures.append(_block("STOP_REQUIRED", "Every trade needs a stop"))
        return failures
    if stop >= entry:
        failures.append(_block("STOP_SIDE", "The stop must sit below the entry"))
        return failures
    if (entry - stop) / entry * 100 < config.min_stop_pct:
        failures.append(_block("STOP_TOO_TIGHT", "That stop is inside normal spread and wiggle"))

    if request.target is not None:
        if request.target <= entry:
            failures.append(_block("TARGET_SIDE", "The target must sit above the entry"))
        elif (request.target - entry) / (entry - stop) < plan.min_reward_risk:
            failures.append(
                _block("REWARD_RISK", f"Reward to risk is under {plan.min_reward_risk}")
            )

    limit = stop_limit_price(stop, instrument.tick_size, config.stop_limit_pct)
    buffer = slippage_points(plan, ctx.events, config)
    worst = worst_case_loss(entry, limit, request.qty, buffer, ctx.rates)
    if worst > plan.max_loss_per_trade:
        failures.append(_block("MAX_LOSS", f"Worst case ₹{worst:.2f} is over the per-trade limit"))
    if worst > plan.loss_limit + snapshot.net_pnl:
        failures.append(_block("DAY_BUDGET", f"Worst case ₹{worst:.2f} is over today's budget"))

    if request.qty % instrument.lot_size != 0:
        failures.append(_block("LOT_SIZE", "Quantity must be whole lots"))
    if instrument.freeze_qty is not None and request.qty > instrument.freeze_qty:
        failures.append(_block("FREEZE_QTY", "Quantity is over the exchange's freeze limit"))

    if snapshot.feed_age is None or snapshot.feed_age > config.feed_max_age_s:
        failures.append(_block("FEED_STALE", "Live prices have stopped"))
    if ctx.ltp is None:
        failures.append(_block("NO_PRICE", "There is no live price for that instrument"))
    elif abs(entry - ctx.ltp) / ctx.ltp * 100 > config.band_pct:
        failures.append(_block("PRICE_BAND", f"The price has moved: last traded {ctx.ltp}"))

    if ctx.mode is Mode.LIVE:
        if not ctx.session_valid:
            failures.append(_block("BROKER_SESSION", "Connect the broker first"))
        if ctx.live_orders_today >= config.live_order_cap:
            failures.append(_block("LIVE_ORDER_CAP", "Today's live order cap is reached"))
    return failures


def evaluate(
    plan: DailyPlan | None,
    snapshot: DaySnapshot,
    request: OrderRequest,
    ctx: OrderContext,
    config: GuardConfig,
) -> Decision:
    """ALLOW, WARN or BLOCK one order. Anything that reduces risk is always allowed."""
    if request.intent in (Intent.STOP, Intent.EXIT):
        return Decision(action=Action.ALLOW)

    if request.intent is Intent.MODIFY_STOP:
        new, current = request.trigger, ctx.current_stop
        if new is None or current is None:
            return Decision(
                action=Action.BLOCK,
                reasons=(_block("STOP_REQUIRED", "A new stop price is required"),),
            )
        if new < current:
            return Decision(
                action=Action.BLOCK, reasons=(_block("STOP_LOOSER", "A stop can only tighten"),)
            )
        if ctx.ltp is not None and new >= ctx.ltp:
            return Decision(
                action=Action.BLOCK,
                reasons=(_block("STOP_ABOVE_MARKET", "A stop must stay below the market"),),
            )
        return Decision(action=Action.ALLOW)

    failures = _entry_failures(plan, snapshot, request, ctx, config)
    if failures:
        return Decision(action=Action.BLOCK, reasons=tuple(failures))
    status = day_status(plan, snapshot, None, ctx.events)
    if status.state is DayState.WARNING:
        return Decision(action=Action.WARN, reasons=status.reasons)
    return Decision(action=Action.ALLOW)
