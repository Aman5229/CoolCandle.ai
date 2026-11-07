"""The guard is the core of the product, so it gets the most tests."""

from datetime import date, time, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.costs import CostRates
from coolcandle.domain.events import MarketEvent
from coolcandle.domain.guard import (
    DayState,
    GuardConfig,
    LockReason,
    OrderContext,
    day_status,
    evaluate,
    lock_trigger,
)
from coolcandle.domain.models import (
    Action,
    DaySnapshot,
    Instrument,
    InstrumentKind,
    Intent,
    Mode,
    OptionType,
    OrderRequest,
    OrderType,
    Side,
)
from coolcandle.domain.plan import DailyPlan, PlanStatus
from coolcandle.domain.sizing import worst_case_loss

DAY = date(2026, 11, 11)
CONFIG = GuardConfig()
RATES = CostRates()
OPTION = Instrument(
    key="nse_fo|1",
    exchange_segment="nse_fo",
    trading_symbol="NIFTY24500CE",
    kind=InstrumentKind.OPTION,
    underlying="NIFTY",
    expiry=date(2026, 11, 17),
    strike=Decimal("24500"),
    option_type=OptionType.CE,
    lot_size=65,
    tick_size=Decimal("0.05"),
    freeze_qty=1755,
)


def plan(**changes: object) -> DailyPlan:
    values: dict[str, object] = {
        "trading_date": DAY,
        "profit_target": Decimal("5000"),
        "loss_limit": Decimal("3000"),
        "max_trades": 3,
        "max_loss_per_trade": Decimal("1500"),
        "status": PlanStatus.LOCKED,
    }
    return DailyPlan.model_validate(values | changes)


def snapshot(hour: int = 10, minute: int = 0, **changes: object) -> DaySnapshot:
    values: dict[str, object] = {"now": ist_datetime(DAY, time(hour, minute)), "feed_age": 0.5}
    return DaySnapshot.model_validate(values | changes)


def entry(**changes: object) -> OrderRequest:
    values: dict[str, object] = {
        "instrument_key": OPTION.key,
        "trading_symbol": OPTION.trading_symbol,
        "exchange_segment": "nse_fo",
        "side": Side.BUY,
        "qty": 65,
        "order_type": OrderType.LIMIT,
        "price": Decimal("100"),
        "stop": Decimal("90"),
        "intent": Intent.ENTRY,
    }
    return OrderRequest.model_validate(values | changes)


def context(**changes: object) -> OrderContext:
    values: dict[str, object] = {
        "mode": Mode.PAPER,
        "rates": RATES,
        "instrument": OPTION,
        "ltp": Decimal("100"),
    }
    return OrderContext.model_validate(values | changes)


def codes(
    decision_plan: DailyPlan | None = None,
    snap: DaySnapshot | None = None,
    req: OrderRequest | None = None,
    ctx: OrderContext | None = None,
) -> set[str]:
    decision = evaluate(
        decision_plan if decision_plan is not None else plan(),
        snap or snapshot(),
        req or entry(),
        ctx or context(),
        CONFIG,
    )
    return {reason.code for reason in decision.reasons}


def test_a_clean_entry_is_allowed() -> None:
    decision = evaluate(plan(), snapshot(), entry(), context(), CONFIG)
    assert decision.action is Action.ALLOW


def test_no_plan_blocks() -> None:
    decision = evaluate(None, snapshot(), entry(), context(), CONFIG)
    assert decision.action is Action.BLOCK
    assert decision.reasons[0].code == "NO_PLAN"


def test_a_draft_plan_blocks() -> None:
    assert "NO_PLAN" in codes(plan(status=PlanStatus.DRAFT))


def test_a_locked_day_blocks_entries() -> None:
    assert "DAY_LOCKED" in codes(ctx=context(lock_reason=LockReason.LOSS_LIMIT))


def test_one_open_trade_at_a_time() -> None:
    assert "OPEN_TRADE" in codes(ctx=context(has_open_trade=True))


def test_only_allowed_underlyings() -> None:
    assert "UNDERLYING" in codes(plan(allowed_underlyings=("SENSEX",)))


def test_only_options_can_be_traded() -> None:
    index = OPTION.model_copy(
        update={"kind": InstrumentKind.INDEX, "option_type": None, "expiry": None, "strike": None}
    )
    assert "INSTRUMENT_KIND" in codes(ctx=context(instrument=index))


def test_selling_options_is_blocked() -> None:
    assert "BUY_ONLY" in codes(req=entry(side=Side.SELL))


def test_a_stop_is_required() -> None:
    assert "STOP_REQUIRED" in codes(req=entry(stop=None))


def test_a_stop_above_the_entry_is_blocked() -> None:
    assert "STOP_SIDE" in codes(req=entry(stop=Decimal("101")))


def test_a_stop_inside_the_spread_is_blocked() -> None:
    assert "STOP_TOO_TIGHT" in codes(req=entry(stop=Decimal("99.9")))


def test_reward_to_risk_must_meet_the_plan() -> None:
    assert "REWARD_RISK" in codes(req=entry(target=Decimal("105")))


def test_a_target_below_the_entry_is_blocked() -> None:
    assert "TARGET_SIDE" in codes(req=entry(target=Decimal("95")))


def test_worst_case_over_the_trade_limit_is_blocked() -> None:
    assert "MAX_LOSS" in codes(req=entry(qty=130, stop=Decimal("85")))


def test_worst_case_over_what_is_left_today_is_blocked() -> None:
    assert "DAY_BUDGET" in codes(snap=snapshot(net_pnl=Decimal("-2500")))


def test_quantity_must_be_whole_lots() -> None:
    assert "LOT_SIZE" in codes(req=entry(qty=70))


def test_quantity_over_freeze_is_blocked() -> None:
    wide = plan(max_loss_per_trade=Decimal("3000"))
    assert "FREEZE_QTY" in codes(wide, req=entry(qty=1820, stop=Decimal("99")))


def test_a_stale_feed_blocks() -> None:
    assert "FEED_STALE" in codes(snap=snapshot(feed_age=4.0))


def test_no_feed_at_all_blocks() -> None:
    assert "FEED_STALE" in codes(snap=snapshot(feed_age=None))


def test_a_price_outside_the_band_blocks() -> None:
    assert "PRICE_BAND" in codes(ctx=context(ltp=Decimal("110")))


def test_entries_close_at_the_cutoff() -> None:
    assert "ENTRY_CUTOFF" in codes(snap=snapshot(14, 30))


def test_entries_wait_for_the_entry_window() -> None:
    assert "ENTRY_WINDOW" in codes(snap=snapshot(9, 16))


def test_a_closed_market_blocks() -> None:
    assert "MARKET_CLOSED" in codes(ctx=context(market_open=False))


def test_trades_used_up_closes_entries_while_one_is_open() -> None:
    snap = snapshot(trades_taken=3, open_trade=True)
    status = day_status(plan(), snap)
    assert status.state is DayState.ENTRIES_CLOSED


def test_cool_off_blocks_after_a_loss() -> None:
    now = ist_datetime(DAY, time(10, 0))
    snap = snapshot(last_loss_at=now - timedelta(minutes=2))
    assert "COOL_OFF" in codes(plan(cool_off_minutes=5), snap=snap)


def test_cool_off_ends() -> None:
    now = ist_datetime(DAY, time(10, 0))
    snap = snapshot(last_loss_at=now - timedelta(minutes=6))
    assert "COOL_OFF" not in codes(plan(cool_off_minutes=5), snap=snap)


def test_needs_attention_blocks_new_entries() -> None:
    assert "NEEDS_ATTENTION" in codes(ctx=context(needs_attention=True))


def test_live_needs_a_broker_session() -> None:
    assert "BROKER_SESSION" in codes(ctx=context(mode=Mode.LIVE, session_valid=False))


def test_live_order_cap() -> None:
    assert "LIVE_ORDER_CAP" in codes(ctx=context(mode=Mode.LIVE, live_orders_today=30))


def test_a_warning_still_allows_the_trade() -> None:
    decision = evaluate(plan(), snapshot(net_pnl=Decimal("-1600")), entry(), context(), CONFIG)
    assert decision.action is Action.WARN
    assert decision.allowed


# --- news risk ---------------------------------------------------------------


def rbi(at: time | None = time(10, 0)) -> MarketEvent:
    return MarketEvent(day=DAY, name="RBI policy decision", at=at)


def test_entries_are_blocked_around_a_scheduled_event() -> None:
    ctx = context(events=(rbi(),))
    assert "EVENT_WINDOW" in codes(snap=snapshot(9, 50), ctx=ctx)
    assert "EVENT_WINDOW" in codes(snap=snapshot(10, 15), ctx=ctx)


def test_entries_are_fine_outside_the_window() -> None:
    assert "EVENT_WINDOW" not in codes(snap=snapshot(10, 16), ctx=context(events=(rbi(),)))


def test_an_event_with_no_time_warns_but_never_blocks() -> None:
    decision = evaluate(plan(), snapshot(), entry(), context(events=(rbi(None),)), CONFIG)
    assert decision.action is Action.WARN


def test_an_event_day_widens_the_slippage_buffer() -> None:
    # Fits on a normal day; the doubled buffer on an event day pushes it over the limit.
    request = entry(qty=130, stop=Decimal("95"))
    tight = plan(max_loss_per_trade=Decimal("1250"))
    assert "MAX_LOSS" not in codes(tight, req=request)
    ctx = context(events=(rbi(time(15, 0)),))
    assert "MAX_LOSS" in codes(tight, req=request, ctx=ctx)


def test_an_exit_is_allowed_inside_an_event_window() -> None:
    request = entry(intent=Intent.EXIT, side=Side.SELL, stop=None)
    decision = evaluate(plan(), snapshot(10, 0), request, context(events=(rbi(),)), CONFIG)
    assert decision.allowed


# --- lock triggers -----------------------------------------------------------


def test_loss_limit_locks() -> None:
    assert lock_trigger(plan(), snapshot(net_pnl=Decimal("-3000"))) is LockReason.LOSS_LIMIT


def test_target_locks() -> None:
    assert lock_trigger(plan(), snapshot(net_pnl=Decimal("5000"))) is LockReason.TARGET


def test_target_locks_even_with_a_trade_open_by_default() -> None:
    snap = snapshot(net_pnl=Decimal("5000"), open_trade=True)
    assert lock_trigger(plan(), snap) is LockReason.TARGET


def test_max_trades_locks_only_when_flat() -> None:
    assert lock_trigger(plan(), snapshot(trades_taken=3)) is LockReason.MAX_TRADES
    assert lock_trigger(plan(), snapshot(trades_taken=3, open_trade=True)) is None


def test_square_off_locks() -> None:
    assert lock_trigger(plan(), snapshot(15, 15)) is LockReason.SQUARE_OFF


def test_a_later_date_locks_a_session_that_never_closed() -> None:
    late = DaySnapshot(now=ist_datetime(date(2026, 11, 12), time(8, 0)))
    assert lock_trigger(plan(), late) is LockReason.SQUARE_OFF


def test_the_loss_limit_wins_over_everything() -> None:
    snap = snapshot(15, 20, net_pnl=Decimal("-3000"), trades_taken=3)
    assert lock_trigger(plan(), snap) is LockReason.LOSS_LIMIT


def test_a_draft_plan_never_locks_the_day() -> None:
    assert lock_trigger(plan(status=PlanStatus.DRAFT), snapshot(net_pnl=Decimal("-9000"))) is None


def test_trailing_at_target_holds_the_lock_while_above_the_floor() -> None:
    trailing = plan(trail_at_target=True, target_giveback_pct=Decimal("20"))
    above = snapshot(net_pnl=Decimal("4500"), peak_net_pnl=Decimal("5200"), open_trade=True)
    assert lock_trigger(trailing, above) is None
    assert day_status(trailing, above).state is DayState.ENTRIES_CLOSED


def test_trailing_at_target_locks_under_the_floor() -> None:
    trailing = plan(trail_at_target=True, target_giveback_pct=Decimal("20"))
    below = snapshot(net_pnl=Decimal("3900"), peak_net_pnl=Decimal("5200"), open_trade=True)
    assert lock_trigger(trailing, below) is LockReason.TARGET


def test_trailing_at_target_locks_once_flat() -> None:
    trailing = plan(trail_at_target=True)
    flat = snapshot(net_pnl=Decimal("4500"), peak_net_pnl=Decimal("5200"))
    assert lock_trigger(trailing, flat) is LockReason.TARGET


# --- risk-reducing orders ----------------------------------------------------


def modify(trigger: str) -> OrderRequest:
    return entry(
        intent=Intent.MODIFY_STOP,
        side=Side.SELL,
        order_type=OrderType.SL,
        trigger=Decimal(trigger),
        stop=None,
    )


def test_a_tighter_stop_is_allowed() -> None:
    ctx = context(current_stop=Decimal("90"), lock_reason=LockReason.TARGET)
    assert evaluate(plan(), snapshot(), modify("95"), ctx, CONFIG).allowed


def test_a_looser_stop_is_blocked() -> None:
    ctx = context(current_stop=Decimal("90"))
    decision = evaluate(plan(), snapshot(), modify("85"), ctx, CONFIG)
    assert decision.reasons[0].code == "STOP_LOOSER"


def test_a_stop_above_the_market_is_blocked() -> None:
    ctx = context(current_stop=Decimal("90"), ltp=Decimal("100"))
    decision = evaluate(plan(), snapshot(), modify("101"), ctx, CONFIG)
    assert decision.reasons[0].code == "STOP_ABOVE_MARKET"


# --- properties --------------------------------------------------------------

money = st.decimals(min_value=-20000, max_value=20000, places=2, allow_nan=False)
price = st.decimals(min_value=5, max_value=500, places=1, allow_nan=False)
lots = st.integers(min_value=1, max_value=20)


@settings(max_examples=300, deadline=None)
@given(net=money, entry_price=price, gap=st.integers(1, 60), count=lots)
def test_an_allowed_entry_can_never_breach_the_loss_limit(
    net: Decimal, entry_price: Decimal, gap: int, count: int
) -> None:
    stop = entry_price - Decimal(gap)
    if stop <= 0:
        return
    request = entry(price=entry_price, stop=stop, qty=count * 65)
    decision = evaluate(
        plan(),
        snapshot(net_pnl=net),
        request,
        context(ltp=entry_price),
        CONFIG,
    )
    if not decision.allowed:
        return
    from coolcandle.domain.exits import stop_limit_price

    limit = stop_limit_price(stop, Decimal("0.05"), CONFIG.stop_limit_pct)
    worst = worst_case_loss(entry_price, limit, count * 65, CONFIG.slippage_points, RATES)
    assert net - worst >= -plan().loss_limit


@settings(max_examples=200, deadline=None)
@given(
    net=money,
    trades=st.integers(0, 25),
    reason=st.sampled_from(list(LockReason)),
    hour=st.integers(9, 15),
)
def test_a_locked_day_never_allows_an_entry(
    net: Decimal, trades: int, reason: LockReason, hour: int
) -> None:
    decision = evaluate(
        plan(),
        snapshot(hour, 30, net_pnl=net, trades_taken=trades),
        entry(),
        context(lock_reason=reason),
        CONFIG,
    )
    assert decision.action is Action.BLOCK


@settings(max_examples=200, deadline=None)
@given(
    net=money,
    reason=st.sampled_from([*LockReason, None]),
    intent=st.sampled_from([Intent.STOP, Intent.EXIT]),
    attention=st.booleans(),
    feed=st.one_of(st.none(), st.floats(0, 1000)),
)
def test_exits_are_always_allowed(
    net: Decimal, reason: LockReason | None, intent: Intent, attention: bool, feed: float | None
) -> None:
    request = entry(intent=intent, side=Side.SELL, stop=None)
    ctx = context(lock_reason=reason, needs_attention=attention, market_open=False)
    snap = snapshot(15, 25, net_pnl=net, feed_age=feed)
    assert evaluate(plan(), snap, request, ctx, CONFIG).allowed
    assert evaluate(None, snap, request, ctx, CONFIG).allowed
