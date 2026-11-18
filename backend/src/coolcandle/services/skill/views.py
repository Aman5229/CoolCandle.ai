"""What the Skill Test screens are sent. Money is rupees; premiums are model prices. All Decimal."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from coolcandle.db.tables import SkillRun, SkillSession
from coolcandle.domain.models import Decision, Frozen
from coolcandle.domain.skill.models import (
    PlanStatus,
    RunStatus,
    SessionPlan,
    SessionState,
    SkillEvent,
    SkillLock,
    SkillSettings,
    SkillTrade,
)
from coolcandle.domain.skill.report import grade
from coolcandle.domain.skill.rules import ZERO, SessionSnapshot, open_pnl, session_state
from coolcandle.domain.skill.run import blown_line
from coolcandle.domain.skill.score import ScoreCard, score
from coolcandle.services.skill.store import FINAL_STATES, trade_status


class RunView(Frozen):
    id: str
    number: int
    start_fund: Decimal
    cash: Decimal
    status: RunStatus
    today: date  # the replayed day the next session plays
    started_at: datetime
    ended_at: datetime | None
    peak_equity: Decimal
    blown_line: Decimal


class SessionSummary(Frozen):
    id: str
    run_id: str
    index: str
    day: date
    state: SessionState
    lock_reason: SkillLock | None
    score: int | None
    grade: str | None
    net_pnl: Decimal | None
    started_at: datetime | None
    ended_at: datetime | None


class TradeView(SkillTrade):
    status: str


class ReplayInfo(Frozen):
    cursor: datetime
    speed: int
    paused: bool
    finished: bool


class PnlView(Frozen):
    realised: Decimal
    unrealised: Decimal
    net: Decimal


class OptionQuote(Frozen):
    """A model price: Black-Scholes on the replayed index, not a quote that ever traded."""

    key: str
    trading_symbol: str
    ltp: Decimal
    bid: Decimal
    ask: Decimal


class ChainRow(Frozen):
    strike: Decimal
    ce: OptionQuote
    pe: OptionQuote


class ChainView(Frozen):
    index: str
    spot: Decimal
    expiry: date
    atm: Decimal
    lot_size: int
    vol: Decimal  # a fraction: 0.14 is 14%
    vol_source: str  # INDIA_VIX, or REALISED when Kotak had no VIX for the day
    rows: tuple[ChainRow, ...]


class SessionView(Frozen):
    id: str
    run_id: str
    index: str
    day: date
    expiry: date
    lot_size: int
    plan: SessionPlan
    plan_status: PlanStatus
    state: SessionState
    lock_reason: SkillLock | None
    started_at: datetime | None
    ended_at: datetime | None
    now: datetime
    fund: Decimal
    start_equity: Decimal | None
    end_equity: Decimal | None
    pnl: PnlView
    trades_taken: int
    trades: tuple[TradeView, ...]
    open_quote: OptionQuote | None  # the open trade's option, at the model price
    score: ScoreCard
    grade: str
    spot: Decimal | None
    prices_fresh: bool
    replay: ReplayInfo | None
    live_locked: bool


class AccountView(Frozen):
    run: RunView | None
    running: SessionSummary | None  # a session that is drafted or started, not yet over
    recent: tuple[SessionSummary, ...]
    best_score: int | None
    sessions_played: int
    runs: int
    indices: tuple[str, ...]  # those with history on disk
    first_day: date | None  # the stored history
    last_day: date | None
    earliest_start: date | None  # a year after the first day, so the chart has a year behind it
    lot_sizes: dict[str, int]
    settings: SkillSettings
    live_locked: bool


class PreviewView(Frozen):
    entry: Decimal | None
    lowest_stop: Decimal | None  # the stops this plan allows, for this many lots
    highest_stop: Decimal | None
    worst_case_loss: Decimal | None
    charges: Decimal | None
    cash_needed: Decimal | None
    reward_risk: Decimal | None
    decision: Decision


def run_view(run: SkillRun, settings: SkillSettings) -> RunView:
    return RunView(
        id=run.id,
        number=run.number,
        start_fund=run.start_fund,
        cash=run.cash,
        status=RunStatus(run.status),
        today=run.today,
        started_at=run.started_at,
        ended_at=run.ended_at,
        peak_equity=run.peak_equity,
        blown_line=blown_line(run.start_fund, settings),
    )


def summary(row: SkillSession) -> SessionSummary:
    net = (
        row.end_equity - row.start_equity
        if row.end_equity is not None and row.start_equity is not None
        else None
    )
    return SessionSummary(
        id=row.id,
        run_id=row.run_id,
        index=row.index,
        day=row.day,
        state=SessionState(row.state),
        lock_reason=SkillLock(row.lock_reason) if row.lock_reason else None,
        score=row.score,
        grade=grade(row.score) if row.score is not None else None,
        net_pnl=net,
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def snapshot(
    trades: Sequence[SkillTrade],
    marks: dict[str, Decimal],
    now: datetime,
    settings: SkillSettings,
) -> SessionSnapshot:
    """`marks` holds each open option's bid: what it could be sold for now."""
    filled = [t for t in trades if t.entry is not None]
    closed = [t for t in filled if t.closed_at is not None]
    unrealised = ZERO
    for trade in filled:
        mark = marks.get(trade.instrument_key)
        if trade.closed_at is None and trade.entry is not None and mark is not None:
            unrealised += open_pnl(trade.entry, mark, trade.qty, settings.rates)
    losses = [t.closed_at for t in closed if t.pnl < 0 and t.closed_at is not None]
    return SessionSnapshot(
        realised_pnl=sum((t.pnl for t in closed), ZERO),
        unrealised_pnl=unrealised,
        trades_taken=len(filled),
        open_trade=any(t.closed_at is None for t in trades),
        last_loss_at=max(losses, default=None),
        now=now,
    )


def live_card(
    row: SkillSession,
    plan: SessionPlan,
    trades: Sequence[SkillTrade],
    events: Sequence[SkillEvent],
    settings: SkillSettings,
) -> ScoreCard:
    lock = SkillLock(row.lock_reason) if row.lock_reason else None
    return score(trades, events, plan, settings, lock=lock, finished=row.state in FINAL_STATES)


def state_now(row: SkillSession, plan: SessionPlan, snap: SessionSnapshot) -> SessionState:
    """The stored state, with ACTIVE refined to the moment: warning, cooling off, or entries
    closed for the time of day."""
    stored = SessionState(row.state)
    if stored is not SessionState.ACTIVE:
        return stored
    return session_state(plan, snap, None, ended=False)


def session_view(
    *,
    row: SkillSession,
    run: SkillRun,
    expiry: date,
    lot_size: int,
    trades: Sequence[SkillTrade],
    events: Sequence[SkillEvent],
    marks: dict[str, Decimal],
    open_quote: OptionQuote | None,
    spot: Decimal | None,
    now: datetime,
    prices_fresh: bool,
    replay: ReplayInfo | None,
    live_locked: bool,
    settings: SkillSettings,
) -> SessionView:
    plan = SessionPlan.model_validate(row.plan)
    snap = snapshot(trades, marks, now, settings)
    card = live_card(row, plan, trades, events, settings)
    return SessionView(
        id=row.id,
        run_id=row.run_id,
        index=row.index,
        day=row.day,
        expiry=expiry,
        lot_size=lot_size,
        plan=plan,
        plan_status=PlanStatus(row.plan_status),
        state=state_now(row, plan, snap),
        lock_reason=SkillLock(row.lock_reason) if row.lock_reason else None,
        started_at=row.started_at,
        ended_at=row.ended_at,
        now=now,
        fund=run.cash,
        start_equity=row.start_equity,
        end_equity=row.end_equity,
        pnl=PnlView(realised=snap.realised_pnl, unrealised=snap.unrealised_pnl, net=snap.net_pnl),
        trades_taken=snap.trades_taken,
        trades=tuple(TradeView(**t.model_dump(), status=trade_status(t)) for t in trades),
        open_quote=open_quote,
        score=card,
        grade=grade(card.score),
        spot=spot,
        prices_fresh=prices_fresh,
        replay=replay,
        live_locked=live_locked,
    )
