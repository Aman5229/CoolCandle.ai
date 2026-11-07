"""V4 trailing rules, V2 setups and the backtest gate, and the V3 number check."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from coolcandle.domain.backtest import (
    Gate,
    TradeResult,
    break_even_win_rate,
    evaluate_gate,
    metrics,
    verdict,
    walk_forward_split,
)
from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.models import (
    Candle,
    Instrument,
    InstrumentKind,
    Mode,
    OptionType,
    Tick,
    Trade,
    TrailingRule,
    TrailKind,
)
from coolcandle.domain.setups.base import Direction, SetupHit
from coolcandle.domain.setups.level_breakout import LevelBreakout
from coolcandle.domain.setups.levels import cluster, find_levels
from coolcandle.domain.setups.opening_range import OpeningRangeBreakout
from coolcandle.domain.signals import SignalConfig, build_signal, option_side, within_band
from coolcandle.domain.trade_state import TradeState
from coolcandle.domain.trailing import next_stop, trails_past_target

DAY = date(2026, 11, 11)


def trade(**changes: object) -> Trade:
    values: dict[str, object] = {
        "id": "t1",
        "account_id": "a1",
        "mode": Mode.PAPER,
        "trading_date": DAY,
        "state": TradeState.OPEN,
        "instrument_key": "k",
        "trading_symbol": "S",
        "exchange_segment": "nse_fo",
        "tick_size": Decimal("0.05"),
        "qty": 65,
        "filled_qty": 65,
        "entry_limit": Decimal("100"),
        "stop_trigger": Decimal("90"),
        "stop_limit": Decimal("88.2"),
        "entry_avg": Decimal("100"),
        "target": Decimal("120"),
    }
    return Trade.model_validate(values | changes)


def bar(minute: int, o: str, h: str, low: str, c: str, interval: str = "5m") -> Candle:
    return Candle(
        instrument_key="idx",
        interval=interval,
        start=ist_datetime(DAY, time(9, 15)) + timedelta(minutes=minute),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def test_breakeven_waits_for_the_trigger() -> None:
    rule = TrailingRule(kind=TrailKind.BREAKEVEN, trigger_points=Decimal("10"))
    assert next_stop(rule, trade(), [], Decimal("109")) is None
    assert next_stop(rule, trade(), [], Decimal("110")) == Decimal("100")


def test_a_trailing_rule_never_loosens_the_stop() -> None:
    rule = TrailingRule(kind=TrailKind.BREAKEVEN, trigger_points=Decimal("10"))
    assert next_stop(rule, trade(stop_trigger=Decimal("105")), [], Decimal("115")) is None


def test_a_trailing_rule_never_goes_above_the_market() -> None:
    rule = TrailingRule(kind=TrailKind.STEP, step_points=Decimal("1"), trail_points=Decimal("0"))
    assert next_stop(rule, trade(), [], Decimal("110")) is None


def test_target_trail_starts_at_the_target() -> None:
    rule = TrailingRule(kind=TrailKind.TARGET_TRAIL, trail_points=Decimal("5"))
    assert next_stop(rule, trade(), [], Decimal("119")) is None
    assert next_stop(rule, trade(), [], Decimal("121")) == Decimal("116")


def test_target_trail_replaces_the_target_exit() -> None:
    assert trails_past_target(TrailingRule(kind=TrailKind.TARGET_TRAIL))
    assert not trails_past_target(TrailingRule(kind=TrailKind.BREAKEVEN))
    assert not trails_past_target(None)


def test_step_trail_moves_in_steps() -> None:
    rule = TrailingRule(kind=TrailKind.STEP, step_points=Decimal("5"), trail_points=Decimal("3"))
    assert next_stop(rule, trade(), [], Decimal("104")) is None
    assert next_stop(rule, trade(), [], Decimal("112")) == Decimal("107")


def test_swing_low_uses_the_lowest_recent_bar() -> None:
    rule = TrailingRule(kind=TrailKind.SWING_LOW, lookback=3)
    bars = [
        bar(0, "100", "104", "95", "103"),
        bar(1, "103", "106", "98", "105"),
        bar(2, "105", "108", "101", "107"),
    ]
    assert next_stop(rule, trade(), bars, Decimal("108")) == Decimal("95")


def test_swing_low_waits_for_enough_bars() -> None:
    rule = TrailingRule(kind=TrailKind.SWING_LOW, lookback=3)
    assert next_stop(rule, trade(), [bar(0, "100", "104", "95", "103")], Decimal("108")) is None


def test_atr_trail_follows_volatility() -> None:
    rule = TrailingRule(kind=TrailKind.ATR, atr_multiple=Decimal("2"))
    bars = [bar(0, "100", "102", "98", "101"), bar(1, "101", "103", "99", "102")]
    assert next_stop(rule, trade(), bars, Decimal("110")) == Decimal("102")


# --- setups ------------------------------------------------------------------


def session(*closes: str) -> list[Candle]:
    """Three opening bars with a range of 24500 to 24520, then bars that close as given."""
    bars = [
        bar(0, "24505", "24520", "24500", "24510"),
        bar(5, "24510", "24515", "24502", "24508"),
        bar(10, "24508", "24518", "24505", "24512"),
    ]
    for i, close in enumerate(closes):
        value = Decimal(close)
        bars.append(
            bar(15 + i * 5, close, str(value + 2), str(value - 2), close),
        )
    return bars


ORB = OpeningRangeBreakout()


def test_opening_range_needs_a_close_beyond_the_range() -> None:
    assert ORB.check("NIFTY", session("24515"), []) is None


def test_a_close_exactly_at_the_high_is_not_a_breakout() -> None:
    assert ORB.check("NIFTY", session("24520"), []) is None


def test_a_wick_is_not_a_breakout() -> None:
    bars = session()
    bars.append(bar(15, "24515", "24530", "24510", "24518"))
    assert ORB.check("NIFTY", bars, []) is None


def test_the_first_close_above_triggers() -> None:
    hit = ORB.check("NIFTY", session("24515", "24525"), [])
    assert hit is not None
    assert hit.direction is Direction.BULLISH
    assert hit.level == Decimal("24520")
    assert hit.index_stop == Decimal("24510")


def test_the_first_close_below_triggers_a_bearish_hit() -> None:
    hit = ORB.check("NIFTY", session("24495"), [])
    assert hit is not None
    assert hit.direction is Direction.BEARISH


def test_a_later_close_is_not_a_second_trigger() -> None:
    assert ORB.check("NIFTY", session("24525", "24530"), []) is None


def test_the_trigger_time_is_when_the_bar_closes() -> None:
    hit = ORB.check("NIFTY", session("24525"), [])
    assert hit is not None
    assert hit.at == ist_datetime(DAY, time(9, 35))


def test_nearby_prices_cluster_into_one_level() -> None:
    levels = cluster([Decimal("100"), Decimal("105"), Decimal("200")], Decimal("10"))
    assert levels[0].touches == 2
    assert levels[0].price == Decimal("102.5")


def history_with_resistance() -> list[Candle]:
    bars = []
    highs = ["10", "12", "20", "12", "10", "11", "20", "11", "10", "12", "20", "12", "10"]
    for i, h in enumerate(highs):
        high = Decimal("24500") + Decimal(h)
        bars.append(bar(i * 5, str(high - 5), str(high), str(high - 8), str(high - 4)))
    return bars


def test_swing_highs_become_a_level() -> None:
    levels = find_levels(history_with_resistance(), width=2)
    assert levels[0].touches >= 2


def test_level_breakout_triggers_on_the_cross() -> None:
    setup = LevelBreakout(min_break_points=Decimal("5"), min_touches=1)
    today = [bar(0, "24500", "24512", "24495", "24510"), bar(5, "24510", "24540", "24508", "24535")]
    hit = setup.check("NIFTY", today, history_with_resistance())
    assert hit is not None
    assert hit.direction is Direction.BULLISH


def test_level_breakout_needs_two_bars() -> None:
    assert LevelBreakout().check("NIFTY", [bar(0, "1", "2", "1", "2")], []) is None


# --- signals -----------------------------------------------------------------

OPTION = Instrument(
    key="k",
    exchange_segment="nse_fo",
    trading_symbol="NIFTY24500CE",
    kind=InstrumentKind.OPTION,
    underlying="NIFTY",
    expiry=DAY,
    strike=Decimal("24500"),
    option_type=OptionType.CE,
    lot_size=65,
)
MOMENT = datetime(2026, 11, 11, 4, 5, tzinfo=UTC)
HIT = SetupHit(
    setup="opening_range",
    version="1",
    underlying="NIFTY",
    direction=Direction.BULLISH,
    level=Decimal("24520"),
    close=Decimal("24530"),
    index_stop=Decimal("24510"),
    at=MOMENT,
    reason="r",
)


def quote(ask: str = "100") -> Tick:
    return Tick(
        instrument_key="k", ts=MOMENT, received_at=MOMENT, ltp=Decimal(ask), ask=Decimal(ask)
    )


def test_code_owns_every_number_in_a_signal() -> None:
    signal = build_signal("s1", HIT, OPTION, quote(), SignalConfig())
    assert signal is not None
    assert signal.entry == Decimal("100")
    assert signal.stop == Decimal("90.00")  # 20 index points at a delta of 0.5
    assert signal.target == Decimal("120.00")  # 2R
    assert signal.expires_at == MOMENT + timedelta(minutes=10)


def test_a_premium_too_small_for_the_risk_gives_no_signal() -> None:
    assert build_signal("s1", HIT, OPTION, quote("5"), SignalConfig()) is None


def test_bullish_means_calls_and_bearish_means_puts() -> None:
    assert option_side(Direction.BULLISH) is OptionType.CE
    assert option_side(Direction.BEARISH) is OptionType.PE


def test_the_band_refuses_a_price_that_has_run() -> None:
    signal = build_signal("s1", HIT, OPTION, quote(), SignalConfig())
    assert signal is not None
    assert within_band(signal, Decimal("102"), Decimal("3"))
    assert not within_band(signal, Decimal("104"), Decimal("3"))


# --- the gate ----------------------------------------------------------------


def results(*pnls: int) -> list[TradeResult]:
    return [
        TradeResult(trading_date=DAY, net_pnl=Decimal(p), r_multiple=Decimal(p) / 100) for p in pnls
    ]


def test_metrics_are_after_costs() -> None:
    summary = metrics(results(300, -100, -100, 200))
    assert summary.trades == 4
    assert summary.win_rate == 50
    assert summary.net_pnl == 300
    assert summary.profit_factor == Decimal("2.5")
    assert summary.worst_losing_streak == 2
    assert summary.worst_streak_loss == 200


def test_no_trades_means_no_crash() -> None:
    assert metrics([]).trades == 0


def test_the_gate_wants_enough_trades() -> None:
    gate = evaluate_gate(metrics(results(300, -100)), Decimal("5000"), Gate())
    assert not gate.passed
    assert any("trades" in failure for failure in gate.failures)


def test_a_good_setup_passes() -> None:
    summary = metrics(results(*([200, -100] * 100)))
    assert evaluate_gate(summary, Decimal("5000"), Gate()).passed


def test_a_high_win_rate_can_still_fail() -> None:
    summary = metrics(results(*([10] * 180 + [-400] * 20)))
    gate = evaluate_gate(summary, Decimal("50000"), Gate())
    assert not gate.passed


def test_the_gate_checks_the_losing_streak() -> None:
    summary = metrics(results(*([500] * 170 + [-100] * 60)))
    assert not evaluate_gate(summary, Decimal("5000"), Gate()).passed


def test_walk_forward_never_shuffles() -> None:
    days = [date(2026, 11, d) for d in (5, 3, 4, 2, 6)]
    tuned, unseen = walk_forward_split(days, Decimal("0.6"))
    assert tuned == [date(2026, 11, d) for d in (2, 3, 4)]
    assert unseen == [date(2026, 11, d) for d in (5, 6)]


def test_a_bigger_target_needs_fewer_wins() -> None:
    assert break_even_win_rate(Decimal("1")) == 50
    assert break_even_win_rate(Decimal("3")) == 25


@pytest.mark.parametrize(
    ("target_hits", "stop_hits", "r", "expected"),
    [
        (51, 49, "1", "Close to a coin flip"),
        (33, 67, "2", "Close to break-even"),
        (60, 40, "1", "Leans toward the target"),
        (20, 80, "1", "Strongly toward the stop"),
        (0, 0, "1", "No trades reached the target or the stop"),
    ],
)
def test_the_verdict_is_honest(target_hits: int, stop_hits: int, r: str, expected: str) -> None:
    assert verdict(target_hits, stop_hits, Decimal(r)) == expected
