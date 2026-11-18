"""The report card at the end of a session: the score first, then the money."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from coolcandle.domain.models import Frozen
from coolcandle.domain.skill.models import SkillSettings, SkillTrade
from coolcandle.domain.skill.run import blown_line
from coolcandle.domain.skill.score import Line, ScoreCard, ScoreLine, r_multiple

ZERO = Decimal("0")


class EquityPoint(Frozen):
    ts: datetime
    equity: Decimal


class ReportCard(Frozen):
    score: int
    grade: str
    net_pnl: Decimal
    trades: int
    win_rate: Decimal | None
    avg_win_r: Decimal | None
    avg_loss_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal
    fees: Decimal
    minutes_in_market: int
    equity_curve: tuple[EquityPoint, ...]
    lines: tuple[ScoreLine, ...]
    biggest_mistake: str | None
    fund_before: Decimal
    fund_after: Decimal
    above_blown_line: Decimal


def grade(score: int) -> str:
    for floor, letter in ((90, "A"), (75, "B"), (60, "C"), (40, "D")):
        if score >= floor:
            return letter
    return "F"


def _average(values: Sequence[Decimal]) -> Decimal | None:
    return (sum(values, ZERO) / len(values)).quantize(Decimal("0.01")) if values else None


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _mistake(card: ScoreCard, trades: Sequence[SkillTrade]) -> str | None:
    """The single costliest line, in one plain sentence."""
    worst = min(
        (line for line in card.lines if line.points < 0), key=lambda line: line.points, default=None
    )
    if worst is None:
        return None
    numbers = {t.id: n for n, t in enumerate(sorted(trades, key=lambda t: t.opened_at), start=1)}
    trade = next((t for t in trades if t.id == worst.trade_id), None)
    which = f" on trade {numbers[trade.id]}" if trade else ""
    if worst.line is Line.STOP_WIDENED and trade is not None:
        result = f", then lost {abs(r_multiple(trade)):.1f}R" if trade.pnl < 0 else ""
        return (
            f"You moved your stop from {_money(trade.stop_at_fill)} to {_money(trade.stop)}"
            f"{which}{result}."
        )
    if worst.line is Line.LOSS_PAST_1R and trade is not None:
        return f"You lost {abs(r_multiple(trade)):.1f}R{which}, more than the risk you planned."
    if worst.line is Line.REVENGE:
        return f"You entered again within minutes of a loss{which}."
    if worst.line is Line.SIZE_UP:
        return f"You took more risk right after a loss{which}."
    if worst.line is Line.BLOCKED:
        return "You tried to trade when the rules said no."
    return f"{worst.label}{which}."


def report(
    trades: Sequence[SkillTrade],
    card: ScoreCard,
    *,
    started_at: datetime,
    fund_before: Decimal,
    start_fund: Decimal,
    settings: SkillSettings,
) -> ReportCard:
    closed = sorted(
        (t for t in trades if t.closed_at is not None and t.entry is not None),
        key=lambda t: t.closed_at or started_at,
    )
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    net = sum((t.pnl for t in closed), ZERO)

    curve = [EquityPoint(ts=started_at, equity=fund_before)]
    peak = fund_before
    drawdown = ZERO
    for trade in closed:
        equity = curve[-1].equity + trade.pnl
        curve.append(EquityPoint(ts=trade.closed_at or started_at, equity=equity))
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    gross_win = sum((t.pnl for t in wins), ZERO)
    gross_loss = -sum((t.pnl for t in losses), ZERO)
    in_market = sum(((t.closed_at or t.opened_at) - t.opened_at for t in closed), timedelta())
    fund_after = fund_before + net
    return ReportCard(
        score=card.score,
        grade=grade(card.score),
        net_pnl=net,
        trades=len(closed),
        win_rate=(Decimal(len(wins)) * 100 / len(closed)).quantize(Decimal("0.1"))
        if closed
        else None,
        avg_win_r=_average([r_multiple(t) for t in wins]),
        avg_loss_r=_average([r_multiple(t) for t in losses]),
        expectancy_r=_average([r_multiple(t) for t in closed]),
        profit_factor=(gross_win / gross_loss).quantize(Decimal("0.01"))
        if gross_loss > 0
        else None,
        max_drawdown=drawdown,
        fees=sum((t.fees for t in closed), ZERO),
        minutes_in_market=int(in_market.total_seconds() // 60),
        equity_curve=tuple(curve),
        lines=card.lines,
        biggest_mistake=_mistake(card, closed),
        fund_before=fund_before,
        fund_after=fund_after,
        above_blown_line=fund_after - blown_line(start_fund, settings),
    )
