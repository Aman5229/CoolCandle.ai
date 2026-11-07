"""Calendar, costs, sizing, trade states, exits and events."""

from datetime import date, time
from decimal import Decimal

import pytest

from coolcandle.domain.calendar import MarketCalendar, ist_datetime
from coolcandle.domain.costs import CostRates, estimate
from coolcandle.domain.events import MarketEvent, blocking_event, has_high_impact, tag_headline
from coolcandle.domain.exits import (
    ExitPlanError,
    StopRule,
    ceil_tick,
    floor_tick,
    plan_exit,
    stop_limit_price,
)
from coolcandle.domain.models import DaySnapshot
from coolcandle.domain.plan import DailyPlan
from coolcandle.domain.sizing import max_lots, worst_case_loss
from coolcandle.domain.trade_state import (
    ACTIVE_STATES,
    InvalidTransition,
    TradeEvent,
    TradeState,
    next_state,
)

TICK = Decimal("0.05")
HOLIDAY = date(2026, 11, 10)
CALENDAR = MarketCalendar([date(2026, 11, 9), HOLIDAY])


def test_weekends_and_holidays_are_not_trading_days() -> None:
    assert not CALENDAR.is_trading_day(date(2026, 11, 7))
    assert not CALENDAR.is_trading_day(HOLIDAY)
    assert CALENDAR.is_trading_day(date(2026, 11, 11))


def test_the_next_trading_day_skips_holidays() -> None:
    assert CALENDAR.next_trading_day(date(2026, 11, 6)) == date(2026, 11, 11)


def test_the_session_rolls_over_at_nine() -> None:
    before = ist_datetime(date(2026, 11, 11), time(8, 59))
    after = ist_datetime(date(2026, 11, 11), time(9, 0))
    assert CALENDAR.trading_date(before) == date(2026, 11, 6)
    assert CALENDAR.trading_date(after) == date(2026, 11, 11)


def test_a_weekend_still_belongs_to_friday() -> None:
    saturday = ist_datetime(date(2026, 11, 7), time(12, 0))
    assert CALENDAR.trading_date(saturday) == date(2026, 11, 6)


def test_a_friday_lock_ends_on_the_next_trading_day() -> None:
    assert CALENDAR.lock_ends_at(date(2026, 11, 6)) == ist_datetime(date(2026, 11, 11), time(9, 0))


def test_market_hours() -> None:
    day = date(2026, 11, 11)
    assert not CALENDAR.is_market_open(ist_datetime(day, time(9, 14)))
    assert CALENDAR.is_market_open(ist_datetime(day, time(9, 15)))
    assert not CALENDAR.is_market_open(ist_datetime(day, time(15, 30)))


def test_charges_add_up() -> None:
    rates = CostRates(
        brokerage_per_order=Decimal("10"),
        stt_sell_pct=Decimal("0.15"),
        exchange_pct=Decimal("0.03503"),
        sebi_pct=Decimal("0.0001"),
        stamp_buy_pct=Decimal("0.003"),
        gst_pct=Decimal("18"),
    )
    # 10,000 bought and 11,000 sold, two orders.
    brokerage, stt = Decimal("20"), Decimal("16.5")
    exchange, sebi = Decimal("7.3563"), Decimal("0.021")
    stamp = Decimal("0.3")
    gst = (brokerage + exchange + sebi) * Decimal("0.18")
    expected = (brokerage + stt + exchange + sebi + stamp + gst).quantize(Decimal("0.01"))
    assert estimate(Decimal("10000"), Decimal("11000"), 2, rates) == expected


def test_nothing_traded_costs_nothing() -> None:
    assert estimate(Decimal(0), Decimal(0), 0, CostRates()) == 0


def plan(**changes: object) -> DailyPlan:
    values: dict[str, object] = {
        "trading_date": date(2026, 11, 11),
        "profit_target": Decimal("5000"),
        "loss_limit": Decimal("3000"),
        "max_trades": 3,
        "max_loss_per_trade": Decimal("1500"),
    }
    return DailyPlan.model_validate(values | changes)


SNAPSHOT = DaySnapshot(now=ist_datetime(date(2026, 11, 11), time(10, 0)))


def test_sizing_finds_the_most_lots_that_fit() -> None:
    size = max_lots(
        plan(),
        SNAPSHOT,
        Decimal("100"),
        Decimal("88.2"),
        65,
        CostRates(),
        slippage_points=Decimal("2"),
    )
    assert size.lots == 1
    assert size.limited_by == "max_loss_per_trade"
    two_lots = worst_case_loss(Decimal("100"), Decimal("88.2"), 130, Decimal("2"), CostRates())
    assert two_lots > plan().max_loss_per_trade


def test_a_day_nearly_used_up_shrinks_the_size() -> None:
    snap = SNAPSHOT.model_copy(update={"net_pnl": Decimal("-2500")})
    size = max_lots(
        plan(), snap, Decimal("100"), Decimal("88.2"), 65, CostRates(), slippage_points=Decimal("2")
    )
    assert size.lots == 0
    assert size.limited_by == "day_budget"


def test_cash_can_decide_the_size() -> None:
    size = max_lots(
        plan(max_loss_per_trade=Decimal("3000")),
        SNAPSHOT,
        Decimal("10"),
        Decimal("9.8"),
        65,
        CostRates(),
        slippage_points=Decimal("0.1"),
        cash=Decimal("1500"),
    )
    assert size.limited_by == "cash"
    assert size.lots == 2


def test_freeze_quantity_can_decide_the_size() -> None:
    size = max_lots(
        plan(),
        SNAPSHOT,
        Decimal("10"),
        Decimal("9.9"),
        65,
        CostRates(),
        slippage_points=Decimal("0.01"),
        freeze_qty=130,
    )
    assert size.lots == 2
    assert size.limited_by == "freeze_qty"


@pytest.mark.parametrize(
    ("state", "event", "after"),
    [
        (TradeState.PROPOSED, TradeEvent.CONFIRM, TradeState.ENTRY_PENDING),
        (TradeState.PROPOSED, TradeEvent.EXPIRE, TradeState.EXPIRED),
        (TradeState.ENTRY_PENDING, TradeEvent.FILL, TradeState.OPEN),
        (TradeState.ENTRY_PENDING, TradeEvent.REJECT, TradeState.REJECTED),
        (TradeState.ENTRY_PENDING, TradeEvent.CANCEL, TradeState.CANCELLED),
        (TradeState.OPEN, TradeEvent.EXIT_TRIGGER, TradeState.EXITING),
        (TradeState.OPEN, TradeEvent.EXIT_FILL, TradeState.CLOSED),
        (TradeState.EXITING, TradeEvent.EXIT_FILL, TradeState.CLOSED),
        (TradeState.EXITING, TradeEvent.ANOMALY, TradeState.NEEDS_ATTENTION),
        (TradeState.NEEDS_ATTENTION, TradeEvent.EXIT_TRIGGER, TradeState.EXITING),
    ],
)
def test_transitions(state: TradeState, event: TradeEvent, after: TradeState) -> None:
    assert next_state(state, event) is after


@pytest.mark.parametrize("state", [TradeState.CLOSED, TradeState.REJECTED, TradeState.CANCELLED])
def test_finished_trades_stay_finished(state: TradeState) -> None:
    for event in TradeEvent:
        with pytest.raises(InvalidTransition):
            next_state(state, event)


def test_active_states_are_the_ones_that_hold_risk() -> None:
    assert TradeState.CLOSED not in ACTIVE_STATES
    assert TradeState.NEEDS_ATTENTION in ACTIVE_STATES


def test_ticks_round_the_safe_way() -> None:
    assert floor_tick(Decimal("88.23"), TICK) == Decimal("88.20")
    assert ceil_tick(Decimal("88.21"), TICK) == Decimal("88.25")


def test_the_stop_limit_sits_two_percent_under_the_trigger() -> None:
    assert stop_limit_price(Decimal("90"), TICK, Decimal("2")) == Decimal("88.20")


def test_the_stop_limit_is_at_least_one_tick_under() -> None:
    assert stop_limit_price(Decimal("1"), TICK, Decimal("2")) == Decimal("0.95")


def test_a_stop_limit_is_never_zero() -> None:
    assert stop_limit_price(Decimal("0.05"), TICK, Decimal("2")) == TICK


def test_exit_plan_from_a_percent_stop() -> None:
    result = plan_exit(Decimal("100"), StopRule.PCT, Decimal("10"), Decimal("2"), TICK)
    assert result.stop == Decimal("90.00")
    assert result.target == Decimal("120.00")
    assert result.reward_risk == 2


def test_exit_plan_refuses_a_stop_above_the_entry() -> None:
    with pytest.raises(ExitPlanError) as refused:
        plan_exit(Decimal("100"), StopRule.PRICE, Decimal("101"), Decimal("2"), TICK)
    assert refused.value.code == "STOP_SIDE"


def test_exit_plan_refuses_a_stop_inside_the_spread() -> None:
    with pytest.raises(ExitPlanError) as refused:
        plan_exit(Decimal("100"), StopRule.PRICE, Decimal("99.9"), Decimal("2"), TICK)
    assert refused.value.code == "STOP_TOO_TIGHT"


def test_exit_plan_needs_candles_for_an_atr_stop() -> None:
    with pytest.raises(ExitPlanError):
        plan_exit(Decimal("100"), StopRule.ATR, Decimal("1.5"), Decimal("2"), TICK)


def event(at: time | None = time(10, 0), **changes: object) -> MarketEvent:
    values: dict[str, object] = {"day": date(2026, 11, 11), "name": "RBI", "at": at}
    return MarketEvent.model_validate(values | changes)


def test_the_block_window_covers_both_sides_of_the_event() -> None:
    day = date(2026, 11, 11)
    events = (event(),)
    assert blocking_event(events, ist_datetime(day, time(9, 45))) is not None
    assert blocking_event(events, ist_datetime(day, time(10, 15))) is not None
    assert blocking_event(events, ist_datetime(day, time(9, 44))) is None
    assert blocking_event(events, ist_datetime(day, time(10, 16))) is None


def test_medium_impact_events_do_not_block() -> None:
    day = date(2026, 11, 11)
    assert blocking_event((event(impact="MEDIUM"),), ist_datetime(day, time(10, 0))) is None


def test_high_impact_is_per_day() -> None:
    assert has_high_impact((event(),), date(2026, 11, 11))
    assert not has_high_impact((event(),), date(2026, 11, 12))


@pytest.mark.parametrize(
    ("headline", "tag"),
    [
        ("RBI holds repo rate at 5.25%", "rbi"),
        ("Fed signals a pause as FOMC meets", "fed"),
        ("Brent crude jumps 6% after attack", "crude"),
        ("FIIs pull out of Indian equities", "flows"),
        ("SEBI tightens F&O rules", "regulator"),
    ],
)
def test_headlines_get_tagged(headline: str, tag: str) -> None:
    assert tag in tag_headline(headline)


def test_an_ordinary_headline_gets_no_tags() -> None:
    assert tag_headline("A nice day for cricket") == ()
