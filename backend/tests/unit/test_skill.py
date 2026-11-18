"""Skill Test's pure rules: the money on a bought option, entry checks, locks, the discipline
score and the report card. Charges come from the live rate card."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.costs import CostRates, round_trip
from coolcandle.domain.models import Action
from coolcandle.domain.plan import PlanError
from coolcandle.domain.skill.models import (
    EventKind,
    ExitKind,
    PlanStatus,
    SessionPlan,
    SessionState,
    SkillEvent,
    SkillLock,
    SkillSettings,
    SkillTrade,
)
from coolcandle.domain.skill.report import grade, report
from coolcandle.domain.skill.rules import (
    EntryCheck,
    SessionSnapshot,
    cash_needed,
    check_entry,
    confirm,
    lock_trigger,
    open_pnl,
    session_state,
    stop_range,
    stricter_edit,
    trade_pnl,
    worst_case_loss,
)
from coolcandle.domain.skill.run import blown_line, check_start_fund, is_blown
from coolcandle.domain.skill.score import Line, score

SETTINGS = SkillSettings()
RATES = CostRates()
TICK = Decimal("0.05")
DAY = datetime(2025, 11, 10).date()
T0 = ist_datetime(DAY, time(10, 0))
PLAN = SessionPlan(
    profit_target=Decimal("2000"),
    loss_limit=Decimal("1000"),
    max_trades=4,
    max_loss_per_trade=Decimal("800"),
    cool_off_minutes=5,
    min_reward_risk=Decimal("1"),
)


# --- money ---------------------------------------------------------------------------------


def test_a_trade_pays_both_sides_charges_from_the_live_rate_card() -> None:
    pnl, charges = trade_pnl(Decimal("100"), Decimal("120"), 65, RATES)
    assert charges == round_trip(Decimal("100"), Decimal("120"), 65, RATES) == Decimal("41.42")
    assert pnl == Decimal("1258.58")  # 20 points of 65 units, less charges


def test_worst_case_is_the_stop_a_tick_worse_plus_charges() -> None:
    # exit 89.95: 10.05 points on 65 units is 653.25, and the charges are 37.68
    assert worst_case_loss(Decimal("100"), Decimal("90"), 65, TICK, RATES) == Decimal("690.93")
    assert worst_case_loss(Decimal("1"), Decimal("0.05"), 65, TICK, RATES) > Decimal("61.75")


def test_an_open_trade_counts_the_charges_already_paid() -> None:
    assert open_pnl(Decimal("100"), Decimal("100"), 65, RATES) < 0
    assert open_pnl(Decimal("100"), Decimal("110"), 65, RATES) < Decimal("650")


def test_an_option_is_paid_for_in_full() -> None:
    assert cash_needed(Decimal("100"), 65, RATES) > Decimal("6500")


# --- the plan ------------------------------------------------------------------------------


def test_starting_takes_two_confirmations_and_the_typed_word() -> None:
    assert confirm(PlanStatus.DRAFT, 1, None) is PlanStatus.CONFIRMING
    with pytest.raises(PlanError):
        confirm(PlanStatus.CONFIRMING, 2, "start")
    assert confirm(PlanStatus.CONFIRMING, 2, "START") is PlanStatus.STARTED
    with pytest.raises(PlanError):
        confirm(PlanStatus.DRAFT, 2, "START")


def test_a_started_plan_only_gets_stricter() -> None:
    stricter = PLAN.model_copy(
        update={
            "loss_limit": Decimal("900"),
            "entry_cutoff": time(13, 0),
            "entry_start": time(9, 30),
        }
    )
    assert {field for field, _, _ in stricter_edit(PLAN, stricter)} == {
        "loss_limit",
        "entry_cutoff",
        "entry_start",
    }
    for looser in (
        {"max_trades": 5},
        {"square_off": time(15, 20)},
        {"entry_start": time(9, 15)},
        {"cool_off_minutes": 0},
    ):
        with pytest.raises(PlanError):
            stricter_edit(PLAN, PLAN.model_copy(update=looser))


@pytest.mark.parametrize(
    "changes",
    [
        {"max_loss_per_trade": "1500"},
        {"entry_start": "09:00"},
        {"entry_start": "14:40"},
        {"square_off": "15:40"},
        {"max_trades": 0},
    ],
)
def test_a_plan_that_does_not_fit_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SessionPlan.model_validate(PLAN.model_dump() | changes)


def test_start_funds_and_the_blown_line() -> None:
    assert check_start_fund(Decimal("100000")) == Decimal("100000")
    with pytest.raises(PlanError):
        check_start_fund(Decimal("75000"))
    assert blown_line(Decimal("100000"), SETTINGS) == Decimal("70000")
    assert is_blown(Decimal("69999"), Decimal("100000"), SETTINGS)
    assert not is_blown(Decimal("70000"), Decimal("100000"), SETTINGS)


# --- the session ---------------------------------------------------------------------------


def snap(**changes: object) -> SessionSnapshot:
    values: dict[str, object] = {"now": T0}
    return SessionSnapshot.model_validate(values | changes)


@pytest.mark.parametrize(
    ("changes", "lock"),
    [
        ({}, None),
        ({"realised_pnl": Decimal("2000")}, SkillLock.TARGET),
        (
            {"realised_pnl": Decimal("-600"), "unrealised_pnl": Decimal("-400")},
            SkillLock.LOSS_LIMIT,
        ),
        ({"trades_taken": 4}, SkillLock.MAX_TRADES),
        ({"trades_taken": 4, "open_trade": True}, None),
        ({"now": ist_datetime(DAY, time(15, 15))}, SkillLock.SQUARE_OFF),
    ],
)
def test_lock_triggers(changes: dict[str, object], lock: SkillLock | None) -> None:
    assert lock_trigger(PLAN, snap(**changes)) is lock


def test_states() -> None:
    assert session_state(PLAN, snap(), None, ended=False) is SessionState.ACTIVE
    assert session_state(PLAN, snap(), SkillLock.TARGET, ended=False) is SessionState.LOCKED
    assert session_state(PLAN, snap(), None, ended=True) is SessionState.ENDED
    loss = snap(last_loss_at=T0 - timedelta(minutes=2))
    assert session_state(PLAN, loss, None, ended=False) is SessionState.COOL_OFF
    early = snap(now=ist_datetime(DAY, time(9, 16)))
    assert session_state(PLAN, early, None, ended=False) is SessionState.ENTRIES_CLOSED
    late = snap(now=ist_datetime(DAY, time(14, 30)))
    assert session_state(PLAN, late, None, ended=False) is SessionState.ENTRIES_CLOSED
    assert session_state(PLAN, snap(trades_taken=3), None, ended=False) is SessionState.WARNING


# --- before every entry --------------------------------------------------------------------


def entry(**changes: object) -> EntryCheck:
    values: dict[str, object] = {
        "lots": 1,
        "qty": 65,
        "entry": Decimal("100"),
        "stop": Decimal("92"),
        "target": Decimal("116"),
        "tick": TICK,
        "state": SessionState.ACTIVE,
        "run_active": True,
        "prices_fresh": True,
        "has_open_trade": False,
        "trades_taken": 0,
        "net_pnl": Decimal("0"),
        "free_cash": Decimal("100000"),
    }
    return EntryCheck.model_validate(values | changes)


def codes(check: EntryCheck) -> set[str]:
    return {reason.code for reason in check_entry(PLAN, check, SETTINGS).reasons}


def test_a_sound_entry_is_allowed() -> None:
    assert check_entry(PLAN, entry(), SETTINGS).action is Action.ALLOW


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"run_active": False}, "RUN_ENDED"),
        ({"state": SessionState.COOL_OFF}, "SESSION_COOL_OFF"),
        ({"state": SessionState.ENTRIES_CLOSED}, "SESSION_ENTRIES_CLOSED"),
        ({"state": SessionState.LOCKED}, "SESSION_LOCKED"),
        ({"prices_fresh": False}, "PRICES_STALE"),
        ({"has_open_trade": True}, "OPEN_TRADE"),
        ({"trades_taken": 4}, "MAX_TRADES"),
        ({"lots": 0, "qty": 0}, "LOTS"),
        ({"stop": None}, "NO_STOP"),
        ({"stop": Decimal("101")}, "STOP_SIDE"),
        ({"stop": Decimal("99.8")}, "STOP_TOO_CLOSE"),
        ({"lots": 2, "qty": 130}, "TRADE_TOO_BIG"),
        ({"net_pnl": Decimal("-500")}, "LOSS_LEFT"),
        ({"target": Decimal("99")}, "TARGET_SIDE"),
        ({"target": Decimal("104")}, "REWARD_RISK"),
        ({"free_cash": Decimal("6000")}, "FUNDS"),
    ],
)
def test_each_rule_blocks_on_its_own(changes: dict[str, object], code: str) -> None:
    assert codes(entry(**changes)) == {code}


# --- the discipline score ------------------------------------------------------------------


def trade(n: int, pnl: str, *, minutes: int, risk: str = "500", **changes: object) -> SkillTrade:
    opened = T0 + timedelta(minutes=minutes)
    values: dict[str, object] = {
        "id": f"t{n}",
        "instrument_key": "skill|NIFTY|2025-11-11|25500|CE",
        "trading_symbol": "NIFTY 11 NOV 25500 CE",
        "qty": 65,
        "entry": Decimal("100"),
        "stop_at_fill": Decimal("92"),
        "stop": Decimal("92"),
        "target": Decimal("116"),
        "exit": Decimal("100"),
        "r_risk": Decimal(risk),
        "pnl": Decimal(pnl),
        "opened_at": opened,
        "closed_at": opened + timedelta(minutes=2),
        "exit_kind": ExitKind.STOP if Decimal(pnl) < 0 else ExitKind.TARGET,
    }
    return SkillTrade.model_validate(values | changes)


def blocked(n: int) -> SkillEvent:
    return SkillEvent(id=f"e{n}", ts=T0 + timedelta(minutes=n), kind=EventKind.BLOCKED)


def lines_of(
    trades: list[SkillTrade], *, lock: SkillLock | None = None, finished: bool = False
) -> set[Line]:
    card = score(trades, [], PLAN, SETTINGS, lock=lock, finished=finished)
    return {line.line for line in card.lines}


def test_a_clean_session_keeps_100() -> None:
    card = score([trade(1, "400", minutes=0)], [], PLAN, SETTINGS)
    assert (card.score, card.lines) == (100, ())


def test_revenge_entry_within_three_minutes_of_a_loss() -> None:
    loss = trade(1, "-500", minutes=0)  # closes at minute 2
    assert Line.REVENGE in lines_of([loss, trade(2, "100", minutes=4)])
    assert Line.REVENGE not in lines_of([loss, trade(2, "100", minutes=6)])


def test_a_widened_stop_costs_fifteen() -> None:
    card = score([trade(1, "-500", minutes=0, stop_widened=True)], [], PLAN, SETTINGS)
    assert card.score == 85


def test_losing_past_one_r() -> None:
    assert Line.LOSS_PAST_1R in lines_of([trade(1, "-600", minutes=0)])
    assert Line.LOSS_PAST_1R not in lines_of([trade(1, "-540", minutes=0)])  # within 1.1R


def test_sizing_up_after_a_loss() -> None:
    loss = trade(1, "-400", minutes=0, risk="400")
    assert Line.SIZE_UP in lines_of([loss, trade(2, "100", minutes=20, risk="700")])
    assert Line.SIZE_UP not in lines_of([loss, trade(2, "100", minutes=20, risk="500")])


def test_blocked_attempts_cost_five_each_up_to_twenty_five() -> None:
    assert score([], [blocked(1), blocked(2)], PLAN, SETTINGS).score == 90
    assert score([], [blocked(n) for n in range(10)], PLAN, SETTINGS).score == 75


def test_cutting_a_winner_early() -> None:
    early = trade(1, "100", minutes=0, exit=Decimal("101"), exit_kind=ExitKind.MANUAL)
    assert Line.EARLY_CUT in lines_of([early])


def test_bonuses_for_stopping_at_target_and_few_green_trades() -> None:
    win = [trade(1, "2000", minutes=0)]
    assert {Line.TARGET_LOCK, Line.FEW_TRADES_GREEN} <= lines_of(
        win, lock=SkillLock.TARGET, finished=True
    )
    capped = score(win, [], PLAN, SETTINGS, lock=SkillLock.TARGET, finished=True)
    assert capped.score == 100  # bonuses never lift it past 100


@st.composite
def sessions(draw: st.DrawFn) -> tuple[list[SkillTrade], list[SkillEvent]]:
    count = draw(st.integers(0, 6))
    trades = [
        trade(
            n,
            str(draw(st.integers(-1500, 1500))),
            minutes=n * draw(st.integers(1, 10)),
            risk=str(draw(st.integers(100, 900))),
            stop_widened=draw(st.booleans()),
        )
        for n in range(count)
    ]
    events = [blocked(n) for n in range(draw(st.integers(0, 8)))]
    return trades, events


@given(sessions(), st.sampled_from([None, *SkillLock]), st.booleans())
def test_the_score_stays_between_0_and_100(
    session: tuple[list[SkillTrade], list[SkillEvent]], lock: SkillLock | None, finished: bool
) -> None:
    trades, events = session
    assert 0 <= score(trades, events, PLAN, SETTINGS, lock=lock, finished=finished).score <= 100


@given(sessions())
def test_a_penalty_event_never_raises_the_score(
    session: tuple[list[SkillTrade], list[SkillEvent]],
) -> None:
    trades, events = session
    before = score(trades, events, PLAN, SETTINGS).score
    after = score(trades, [*events, blocked(99)], PLAN, SETTINGS).score
    assert after <= before


@given(sessions())
def test_the_same_session_always_scores_the_same(
    session: tuple[list[SkillTrade], list[SkillEvent]],
) -> None:
    trades, events = session
    first = score(trades, events, PLAN, SETTINGS)
    again = score(list(reversed(trades)), list(reversed(events)), PLAN, SETTINGS)
    assert first == again


# --- the report card -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("points", "letter"), [(95, "A"), (90, "A"), (80, "B"), (60, "C"), (45, "D"), (10, "F")]
)
def test_grades(points: int, letter: str) -> None:
    assert grade(points) == letter


def test_the_report_counts_r_and_names_the_biggest_mistake() -> None:
    trades = [
        trade(1, "1000", minutes=0),  # +2R
        trade(2, "-900", minutes=10, stop=Decimal("88.5"), stop_widened=True),  # -1.8R
    ]
    card = score(trades, [], PLAN, SETTINGS)
    result = report(
        trades,
        card,
        started_at=T0,
        fund_before=Decimal("100000"),
        start_fund=Decimal("100000"),
        settings=SETTINGS,
    )
    assert (result.trades, result.net_pnl, result.win_rate) == (2, Decimal("100"), Decimal("50.0"))
    assert (result.avg_win_r, result.avg_loss_r) == (Decimal("2.00"), Decimal("-1.80"))
    assert result.max_drawdown == Decimal("900")
    assert result.fund_after == Decimal("100100")
    assert result.above_blown_line == Decimal("30100")
    assert (
        result.biggest_mistake == "You moved your stop from 92 to 88.5 on trade 2, then lost 1.8R."
    )
    assert result.grade == grade(card.score)


# --- the stops a plan allows ---------------------------------------------------------------


def test_the_stop_range_runs_from_the_per_trade_limit_to_half_a_percent_below() -> None:
    allowed = stop_range(Decimal("119.30"), 65, TICK, Decimal("500"), SETTINGS)
    assert allowed is not None
    lowest, highest = allowed
    assert highest == Decimal("118.70")  # 0.5% below 119.30 is 118.70, on the 0.05 grid
    assert worst_case_loss(Decimal("119.30"), lowest, 65, TICK, RATES) <= 500
    assert worst_case_loss(Decimal("119.30"), lowest - TICK, 65, TICK, RATES) > 500
    assert Decimal("111") < lowest < Decimal("113")  # about seven points of room on one lot


def test_no_stop_fits_when_even_the_tightest_costs_too_much() -> None:
    assert stop_range(Decimal("119.30"), 65 * 20, TICK, Decimal("500"), SETTINGS) is None


def test_a_refused_stop_says_which_stops_would_do() -> None:
    plan = PLAN.model_copy(update={"max_loss_per_trade": Decimal("500")})
    check = entry(entry=Decimal("119.30"), stop=Decimal("120"), target=None)
    message = check_entry(plan, check, SETTINGS).reasons[0].message
    assert message.startswith("The stop must be below the buy price, 119.30: your plan allows ")
    assert message.endswith(" to 118.70")
    closed = check_entry(plan, entry(state=SessionState.ENTRIES_CLOSED), SETTINGS)
    assert closed.reasons[0].message == "Entries are open from 09:20 to 14:30 in your plan"
