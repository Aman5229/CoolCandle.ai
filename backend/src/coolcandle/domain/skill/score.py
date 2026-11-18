"""The discipline score. A session starts at 100 and loses points for the habits that cost traders
money. It never goes below 0 or above 100, and the same trades and events always give the same
score. Every line names the trade or event behind it, so the report can show which one cost what."""

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from coolcandle.domain.models import Frozen
from coolcandle.domain.skill.models import (
    EventKind,
    ExitKind,
    SessionPlan,
    SkillEvent,
    SkillLock,
    SkillSettings,
    SkillTrade,
)


class Line(StrEnum):
    REVENGE = "REVENGE"
    STOP_WIDENED = "STOP_WIDENED"
    LOSS_PAST_1R = "LOSS_PAST_1R"
    SIZE_UP = "SIZE_UP"
    BLOCKED = "BLOCKED"
    EARLY_CUT = "EARLY_CUT"
    TARGET_LOCK = "TARGET_LOCK"
    FEW_TRADES_GREEN = "FEW_TRADES_GREEN"


# The one table to tune: points for each behaviour, and what it means in plain words.
WEIGHTS: dict[Line, tuple[int, str]] = {
    Line.REVENGE: (-10, "Entered within minutes of a loss"),
    Line.STOP_WIDENED: (-15, "Moved the stop further away"),
    Line.LOSS_PAST_1R: (-10, "Lost more than the planned risk"),
    Line.SIZE_UP: (-10, "Took more risk right after a loss"),
    Line.BLOCKED: (-5, "Tried to trade when the rules said no"),
    Line.EARLY_CUT: (-3, "Cut a winner well short of its target"),
    Line.TARGET_LOCK: (5, "Stopped at the profit target"),
    Line.FEW_TRADES_GREEN: (5, "Few trades and finished green"),
}
BLOCKED_CAP = -25
LOSS_TOLERANCE = Decimal("1.1")
SIZE_UP_RATIO = Decimal("1.5")
EARLY_CUT_PROFIT_R = Decimal("0.5")
EARLY_CUT_TARGET_R = Decimal("1.5")


class ScoreLine(Frozen):
    line: Line
    points: int
    label: str
    trade_id: str | None = None
    event_id: str | None = None


class ScoreCard(Frozen):
    score: int
    lines: tuple[ScoreLine, ...]


def _line(kind: Line, trade_id: str | None = None, event_id: str | None = None) -> ScoreLine:
    points, label = WEIGHTS[kind]
    return ScoreLine(line=kind, points=points, label=label, trade_id=trade_id, event_id=event_id)


def r_multiple(trade: SkillTrade) -> Decimal:
    return trade.pnl / trade.r_risk if trade.r_risk > 0 else Decimal("0")


def _target_left_r(trade: SkillTrade) -> Decimal | None:
    """How far, in R, the target still was when the trade was closed by hand."""
    if trade.target is None or trade.exit is None or trade.entry is None:
        return None
    per_coin_risk = abs(trade.entry - trade.stop_at_fill)
    if per_coin_risk == 0:
        return None
    left = trade.target - trade.exit
    return left / per_coin_risk


def score(
    trades: Sequence[SkillTrade],
    events: Sequence[SkillEvent],
    plan: SessionPlan,
    settings: SkillSettings,
    *,
    lock: SkillLock | None = None,
    finished: bool = False,
) -> ScoreCard:
    filled = sorted((t for t in trades if t.entry is not None), key=lambda t: (t.opened_at, t.id))
    lines: list[ScoreLine] = []
    revenge_window = timedelta(minutes=settings.revenge_minutes)

    for index, trade in enumerate(filled):
        earlier = [
            t for t in filled[:index] if t.closed_at is not None and t.closed_at <= trade.opened_at
        ]
        losses = [t for t in earlier if t.pnl < 0]
        if any(trade.opened_at - t.closed_at <= revenge_window for t in losses if t.closed_at):
            lines.append(_line(Line.REVENGE, trade.id))
        if trade.stop_widened:
            lines.append(_line(Line.STOP_WIDENED, trade.id))
        if trade.closed_at is not None and -trade.pnl > trade.r_risk * LOSS_TOLERANCE:
            lines.append(_line(Line.LOSS_PAST_1R, trade.id))
        previous = max(earlier, key=lambda t: (t.closed_at, t.id), default=None)
        if (
            previous is not None
            and previous.pnl < 0
            and trade.r_risk > previous.r_risk * SIZE_UP_RATIO
        ):
            lines.append(_line(Line.SIZE_UP, trade.id))
        target_left = _target_left_r(trade)
        if (
            trade.exit_kind is ExitKind.MANUAL
            and trade.pnl > 0
            and r_multiple(trade) < EARLY_CUT_PROFIT_R
            and target_left is not None
            and target_left >= EARLY_CUT_TARGET_R
        ):
            lines.append(_line(Line.EARLY_CUT, trade.id))

    blocked = sorted((e for e in events if e.kind is EventKind.BLOCKED), key=lambda e: (e.ts, e.id))
    lines.extend(_line(Line.BLOCKED, event_id=e.id) for e in blocked)

    if lock is SkillLock.TARGET:
        lines.append(_line(Line.TARGET_LOCK))
    net = sum((t.pnl for t in filled if t.closed_at is not None), Decimal("0"))
    if finished and filled and len(filled) * 2 <= plan.max_trades and net > 0:
        lines.append(_line(Line.FEW_TRADES_GREEN))

    blocked_points = max(
        sum(line.points for line in lines if line.line is Line.BLOCKED), BLOCKED_CAP
    )
    other_points = sum(line.points for line in lines if line.line is not Line.BLOCKED)
    total = min(100, max(0, 100 + blocked_points + other_points))
    return ScoreCard(score=total, lines=tuple(lines))
