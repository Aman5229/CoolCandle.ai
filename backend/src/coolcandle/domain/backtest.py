"""Backtest maths: the gate a setup must pass, and an honest one-line verdict."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from coolcandle.domain.models import ZERO, Frozen


class TradeResult(Frozen):
    trading_date: date
    net_pnl: Decimal
    r_multiple: Decimal


class Metrics(Frozen):
    trades: int
    wins: int
    win_rate: Decimal
    net_pnl: Decimal
    avg_net_pnl: Decimal
    avg_r: Decimal
    profit_factor: Decimal | None
    worst_losing_streak: int
    worst_streak_loss: Decimal


@dataclass(frozen=True)
class Gate:
    min_trades: int = 150
    min_profit_factor: Decimal = Decimal("1.3")


class GateResult(Frozen):
    passed: bool
    failures: tuple[str, ...]


def metrics(results: Sequence[TradeResult]) -> Metrics:
    count = len(results)
    wins = [r for r in results if r.net_pnl > 0]
    gross_win = sum((r.net_pnl for r in wins), ZERO)
    gross_loss = -sum((r.net_pnl for r in results if r.net_pnl < 0), ZERO)
    net = gross_win - gross_loss

    worst_streak = streak = 0
    worst_loss = streak_loss = ZERO
    for result in results:
        if result.net_pnl < 0:
            streak += 1
            streak_loss -= result.net_pnl
            worst_streak = max(worst_streak, streak)
            worst_loss = max(worst_loss, streak_loss)
        else:
            streak, streak_loss = 0, ZERO

    return Metrics(
        trades=count,
        wins=len(wins),
        win_rate=Decimal(len(wins)) / count * 100 if count else ZERO,
        net_pnl=net,
        avg_net_pnl=net / count if count else ZERO,
        avg_r=sum((r.r_multiple for r in results), ZERO) / count if count else ZERO,
        profit_factor=gross_win / gross_loss if gross_loss > 0 else None,
        worst_losing_streak=worst_streak,
        worst_streak_loss=worst_loss,
    )


def evaluate_gate(result: Metrics, daily_loss_limit: Decimal, gate: Gate) -> GateResult:
    """The profit-after-costs gate, to be run on data the setup was not tuned on."""
    failures = []
    if result.trades < gate.min_trades:
        failures.append(f"Only {result.trades} trades, needs {gate.min_trades}")
    if result.avg_net_pnl <= 0:
        failures.append("Average net P&L per trade is not positive after costs")
    if result.profit_factor is None or result.profit_factor < gate.min_profit_factor:
        failures.append(f"Profit factor is under {gate.min_profit_factor}")
    if result.worst_streak_loss > daily_loss_limit:
        failures.append("The worst losing streak is more than the daily loss limit absorbs")
    return GateResult(passed=not failures, failures=tuple(failures))


def walk_forward_split(
    days: Sequence[date], train_fraction: Decimal
) -> tuple[list[date], list[date]]:
    """Oldest days to tune on, newest days to test on. Never shuffled."""
    ordered = sorted(days)
    cut = int(len(ordered) * train_fraction)
    return ordered[:cut], ordered[cut:]


def break_even_win_rate(target_r: Decimal) -> Decimal:
    """A bigger target needs to win less often: 1 in (1 + R)."""
    return 100 / (1 + target_r)


def verdict(target_hits: int, stop_hits: int, target_r: Decimal) -> str:
    """How the real record compares with break-even, in plain words. Not a prediction."""
    decided = target_hits + stop_hits
    if decided == 0:
        return "No trades reached the target or the stop"
    edge = Decimal(target_hits) / decided * 100 - break_even_win_rate(target_r)
    if abs(edge) < 5:
        return "Close to a coin flip" if target_r == 1 else "Close to break-even"
    side = "target" if edge > 0 else "stop"
    strength = "Strongly toward" if abs(edge) >= 15 else "Leans toward"
    return f"{strength} the {side}"
