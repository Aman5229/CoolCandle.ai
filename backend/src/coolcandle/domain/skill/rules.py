"""Skill Test's rules: the money on a bought option, the checks before every entry, and when a
replayed day locks. They follow the live day (a stop on every trade, worst-case loss inside the
plan, an entry window and a square-off, a lock that holds), with charges from the live rate card.
The live guard itself is untouched."""

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from pydantic import AwareDatetime

from coolcandle.domain.calendar import to_ist
from coolcandle.domain.costs import CostRates, estimate, money, round_trip
from coolcandle.domain.models import Action, Decision, Frozen, Reason
from coolcandle.domain.plan import PlanError, tighter_only
from coolcandle.domain.skill.models import (
    PlanStatus,
    SessionPlan,
    SessionState,
    SkillLock,
    SkillSettings,
)

ZERO = Decimal("0")
CONFIRM_WORD = "START"
_LOWER_IS_STRICTER = (
    "profit_target",
    "loss_limit",
    "max_trades",
    "max_loss_per_trade",
    "entry_cutoff",
    "square_off",
)
_HIGHER_IS_STRICTER = ("cool_off_minutes", "min_reward_risk", "entry_start")


# --- money ---------------------------------------------------------------------------------


def worst_case_loss(
    entry: Decimal, stop: Decimal, qty: int, tick: Decimal, rates: CostRates
) -> Decimal:
    """Rupees lost if the stop fills a tick worse than its trigger, with both sides' charges."""
    exit_price = max(stop - tick, tick)
    return money((entry - exit_price) * qty + round_trip(entry, exit_price, qty, rates))


def trade_pnl(
    entry: Decimal, exit_price: Decimal, qty: int, rates: CostRates
) -> tuple[Decimal, Decimal]:
    """(net P&L, charges) for a closed trade."""
    charges = round_trip(entry, exit_price, qty, rates)
    return money((exit_price - entry) * qty - charges), charges


def open_pnl(entry: Decimal, mark: Decimal, qty: int, rates: CostRates) -> Decimal:
    """An open trade's rupees at `mark`, after the charges already paid to enter."""
    return money((mark - entry) * qty - estimate(entry * qty, ZERO, 1, rates))


def _on_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Down to the tick grid."""
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def stop_range(
    entry: Decimal,
    qty: int,
    tick: Decimal,
    budget: Decimal,
    settings: SkillSettings,
) -> tuple[Decimal, Decimal] | None:
    """(lowest, highest) stop an entry at `entry` allows. The highest keeps the stop at least
    `min_stop_pct` below the entry; the lowest is the furthest a stop can go before its worst
    case costs more than `budget` (the per-trade limit, or what is left of the loss limit). None
    when even the tightest stop costs too much."""
    highest = _on_tick(entry - max(entry * settings.min_stop_pct / 100, tick), tick)
    if qty < 1 or highest < tick:
        return None
    if worst_case_loss(entry, highest, qty, tick, settings.rates) > budget:
        return None
    low, high = 1, int(highest / tick)  # in ticks; the worst case falls as the stop rises
    while low < high:
        middle = (low + high) // 2
        if worst_case_loss(entry, tick * middle, qty, tick, settings.rates) <= budget:
            high = middle
        else:
            low = middle + 1
    return tick * low, highest


def cash_needed(entry: Decimal, qty: int, rates: CostRates) -> Decimal:
    """A bought option is paid for in full: the premium and the charges to enter."""
    return money(entry * qty + estimate(entry * qty, ZERO, 1, rates))


# --- the plan ------------------------------------------------------------------------------


def confirm(status: PlanStatus, step: int, typed: str | None) -> PlanStatus:
    """DRAFT -> CONFIRMING on step 1, CONFIRMING -> STARTED on step 2 with the typed word."""
    if step == 1 and status is PlanStatus.DRAFT:
        return PlanStatus.CONFIRMING
    if step == 2 and status is PlanStatus.CONFIRMING:
        if typed != CONFIRM_WORD:
            raise PlanError("CONFIRM_WORD", f"Type {CONFIRM_WORD} to start the session")
        return PlanStatus.STARTED
    raise PlanError("CONFIRM_STEP", f"Step {step} is not valid while the plan is {status}")


def stricter_edit(old: SessionPlan, new: SessionPlan) -> list[tuple[str, str, str]]:
    """Once started, a session plan only gets stricter, as the live plan does."""
    return tighter_only(
        old, new, _LOWER_IS_STRICTER, _HIGHER_IS_STRICTER, "The session plan is locked"
    )


def plan_fits_fund(plan: SessionPlan, free_cash: Decimal) -> str | None:
    if plan.loss_limit > free_cash:
        return "The loss limit is more than the fund has left"
    return None


# --- the session ---------------------------------------------------------------------------


class SessionSnapshot(Frozen):
    realised_pnl: Decimal = ZERO  # rupees, after charges
    unrealised_pnl: Decimal = ZERO
    trades_taken: int = 0
    open_trade: bool = False
    last_loss_at: AwareDatetime | None = None
    now: AwareDatetime  # the replay's clock

    @property
    def net_pnl(self) -> Decimal:
        return self.realised_pnl + self.unrealised_pnl


def lock_trigger(plan: SessionPlan, snap: SessionSnapshot) -> SkillLock | None:
    if snap.net_pnl >= plan.profit_target:
        return SkillLock.TARGET
    if snap.net_pnl <= -plan.loss_limit:
        return SkillLock.LOSS_LIMIT
    if snap.trades_taken >= plan.max_trades and not snap.open_trade:
        return SkillLock.MAX_TRADES
    if to_ist(snap.now).time() >= plan.square_off:
        return SkillLock.SQUARE_OFF
    return None


def cooling_off(plan: SessionPlan, snap: SessionSnapshot) -> bool:
    if snap.last_loss_at is None or plan.cool_off_minutes == 0:
        return False
    return snap.now < snap.last_loss_at + timedelta(minutes=plan.cool_off_minutes)


def entries_open(plan: SessionPlan, snap: SessionSnapshot) -> bool:
    return plan.entry_start <= to_ist(snap.now).time() < plan.entry_cutoff


def session_state(
    plan: SessionPlan, snap: SessionSnapshot, lock: SkillLock | None, ended: bool
) -> SessionState:
    if ended:
        return SessionState.ENDED
    if lock is not None:
        return SessionState.LOCKED
    if cooling_off(plan, snap):
        return SessionState.COOL_OFF
    if not entries_open(plan, snap):
        return SessionState.ENTRIES_CLOSED
    near_loss = snap.net_pnl <= -plan.loss_limit * Decimal("0.8")
    if near_loss or plan.max_trades - snap.trades_taken == 1:
        return SessionState.WARNING
    return SessionState.ACTIVE


# --- before every entry --------------------------------------------------------------------


class EntryCheck(Frozen):
    lots: int
    qty: int  # lots times the lot size
    entry: Decimal  # the premium it would fill at
    stop: Decimal | None
    target: Decimal | None = None
    tick: Decimal
    state: SessionState
    run_active: bool
    prices_fresh: bool
    has_open_trade: bool
    trades_taken: int
    net_pnl: Decimal
    free_cash: Decimal


def _block(code: str, message: str) -> Reason:
    return Reason(code=code, message=message)


_STATE_MESSAGES = {
    SessionState.DRAFT: "Confirm the session plan first",
    SessionState.COOL_OFF: "Cooling off after a loss",
    SessionState.ENTRIES_CLOSED: "Entries are closed at this time of day",
    SessionState.LOCKED: "The session is locked",
    SessionState.ENDED: "The session has ended",
}


def session_block(state: SessionState, run_active: bool) -> Reason | None:
    """Why this session takes no entry at all right now, if it doesn't."""
    if not run_active:
        return _block("RUN_ENDED", "This run has ended. Start a new one.")
    if state not in (SessionState.ACTIVE, SessionState.WARNING):
        return _block(f"SESSION_{state}", _STATE_MESSAGES[state])
    return None


def check_entry(plan: SessionPlan, check: EntryCheck, settings: SkillSettings) -> Decision:
    """Every reason an entry is refused, in the order the spec lists them."""
    reasons: list[Reason] = []
    blocked = session_block(check.state, check.run_active)
    if blocked is not None:
        if check.state is SessionState.ENTRIES_CLOSED:
            window = f"{plan.entry_start:%H:%M} to {plan.entry_cutoff:%H:%M}"
            blocked = _block(blocked.code, f"Entries are open from {window} in your plan")
        reasons.append(blocked)
    if not check.prices_fresh:
        reasons.append(_block("PRICES_STALE", "The replay is paused"))
    if check.has_open_trade:
        reasons.append(_block("OPEN_TRADE", "One trade at a time"))
    if check.trades_taken >= plan.max_trades:
        reasons.append(_block("MAX_TRADES", "No trades left in this session"))
    if check.lots < 1:
        reasons.append(_block("LOTS", "Buy at least one lot"))

    loss_left = plan.loss_limit + min(check.net_pnl, ZERO)
    allowed = stop_range(
        check.entry, check.qty, check.tick, min(plan.max_loss_per_trade, loss_left), settings
    )
    fits = f": your plan allows {allowed[0]} to {allowed[1]}" if allowed else ""
    if check.stop is None:
        reasons.append(_block("NO_STOP", f"Every trade needs a stop{fits}"))
    elif check.stop >= check.entry:
        reasons.append(
            _block("STOP_SIDE", f"The stop must be below the buy price, {check.entry}{fits}")
        )
    elif check.entry - check.stop < check.entry * settings.min_stop_pct / 100:
        reasons.append(
            _block(
                "STOP_TOO_CLOSE",
                f"Keep the stop at least {settings.min_stop_pct}% below the buy price{fits}",
            )
        )
    else:
        worst = worst_case_loss(check.entry, check.stop, check.qty, check.tick, settings.rates)
        if worst > plan.max_loss_per_trade:
            reasons.append(
                _block(
                    "TRADE_TOO_BIG",
                    f"This stop could lose ₹{worst}, above your ₹{plan.max_loss_per_trade} a "
                    f"trade{fits}"
                    if allowed
                    else f"This stop could lose ₹{worst}, above your ₹{plan.max_loss_per_trade} a "
                    "trade, and no stop on this option fits: pick a cheaper strike",
                )
            )
        elif worst > loss_left:
            reasons.append(
                _block(
                    "LOSS_LEFT",
                    f"This stop could lose ₹{worst}, more than the ₹{loss_left} left of your "
                    f"loss limit{fits}",
                )
            )
        if check.target is not None:
            reward = check.target - check.entry
            if reward <= 0:
                reasons.append(_block("TARGET_SIDE", "The target must be above the entry"))
            elif reward / (check.entry - check.stop) < plan.min_reward_risk:
                reasons.append(
                    _block("REWARD_RISK", f"Reward to risk is under {plan.min_reward_risk}")
                )
    if cash_needed(check.entry, check.qty, settings.rates) > check.free_cash:
        reasons.append(_block("FUNDS", "Not enough funds to pay for the option"))

    return Decision(action=Action.BLOCK if reasons else Action.ALLOW, reasons=tuple(reasons))
