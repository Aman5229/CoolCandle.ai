"""Replays recorded days through the real guard, OrderService and paper broker.

A throwaway in-memory database per day means a backtest can never touch live state.
"""

from datetime import date, time, timedelta
from decimal import Decimal

from coolcandle.adapters.clock import ManualClock
from coolcandle.adapters.market.replay.source import (
    ReplayMarket,
    read_day,
    read_instruments,
    recorded_days,
)
from coolcandle.config import Settings
from coolcandle.db.session import make_engine, make_sessions
from coolcandle.db.tables import Base, BrokerAccount, Experiment, User
from coolcandle.domain.backtest import (
    Gate,
    TradeResult,
    evaluate_gate,
    metrics,
    verdict,
    walk_forward_split,
)
from coolcandle.domain.calendar import ist_datetime
from coolcandle.domain.costs import round_trip
from coolcandle.domain.models import ExitReason, Mode, OrderSource, Trade
from coolcandle.domain.setups.level_breakout import LevelBreakout
from coolcandle.domain.setups.opening_range import OpeningRangeBreakout
from coolcandle.domain.signals import Signal
from coolcandle.main import Container, build, cost_rates
from coolcandle.ports.signal_source import SignalSource
from coolcandle.services.orders.service import TradeError

MEMORY_DB = "sqlite+aiosqlite:///:memory:"
# Wide limits, so the replay measures the setup and not one day's plan.
TEST_PLAN: dict[str, Decimal | int] = {
    "profit_target": Decimal("1000000"),
    "loss_limit": Decimal("1000000"),
    "max_trades": 20,
    "max_loss_per_trade": Decimal("500000"),
}
SETUPS: dict[str, SignalSource] = {
    "opening_range": OpeningRangeBreakout(),
    "level_breakout": LevelBreakout(),
}
TRAIN_FRACTION = Decimal("0.7")


def trade_result(trade: Trade, container: Container) -> TradeResult:
    """Net of full charges, in rupees and in multiples of the risk taken."""
    entry = trade.entry_avg or trade.entry_limit
    exit_price = trade.exit_avg or entry
    charges = round_trip(entry, exit_price, trade.filled_qty, cost_rates(container.settings))
    net = trade.realised_pnl - charges
    risk = (entry - trade.stop_trigger) * trade.filled_qty
    return TradeResult(
        trading_date=trade.trading_date,
        net_pnl=net,
        r_multiple=net / risk if risk > 0 else Decimal(0),
    )


async def _prepare(settings: Settings, day: date, setup: SignalSource) -> tuple[Container, str]:
    clock = ManualClock(ist_datetime(day, time(9, 0)))
    instruments = read_instruments(settings.data_dir, day)
    market = ReplayMarket(settings.data_dir, day, instruments)
    replay_settings = settings.model_copy(update={"database_url": MEMORY_DB})
    container = build(replay_settings, clock=clock, market=market)
    engine = container.engine
    engine.recorder = None
    container.signals.setups = [setup]

    db_engine = container.db.kw["bind"]
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with container.db() as session, session.begin():
        user = User(email="backtest@local", password_hash="-", totp_secret_enc="-")
        session.add(user)
        await session.flush()
        account = BrokerAccount(user_id=user.id)
        session.add(account)
        await session.flush()
        account_id = account.id

    await engine.feed.use(market)
    engine._indices = {i.key: i.underlying for i in container.instruments.indices()}
    await engine.refresh_accounts()
    await container.plans.save_draft(account_id, dict(TEST_PLAN))
    await container.plans.confirm(account_id, 1, None)
    await container.plans.confirm(account_id, 2, "LOCK")
    return container, account_id


async def run_day(settings: Settings, setup: SignalSource, day: date) -> list[TradeResult]:
    container, account_id = await _prepare(settings, day, setup)
    engine = container.engine
    clock = container.clock
    assert isinstance(clock, ManualClock)

    async def take(owner: str, signal: Signal) -> None:
        """The replay stands in for the tap: paper only, on a throwaway database."""
        try:
            await container.signals.take(owner, Mode.PAPER, signal.id, 1, OrderSource.SIGNAL)
        except TradeError:
            pass

    container.signals.listeners.append(take)
    next_check = None
    for tick in read_day(settings.data_dir, day):
        clock.set(tick.received_at)
        await engine.on_tick(tick)
        if next_check is None or tick.received_at >= next_check:
            next_check = tick.received_at + timedelta(seconds=1)
            await engine.pump()
            await container.orders.housekeeping()
            await engine.check_all()
    await container.orders.flatten(account_id, Mode.PAPER, ExitReason.SQUARE_OFF)
    await engine.pump()

    trades = await container.journal.trades(account_id, Mode.PAPER, day)
    await container.db.kw["bind"].dispose()
    return [trade_result(t, container) for t in trades if t.closed_at is not None]


async def run_backtest(
    settings: Settings, setup_name: str, start: date, end: date, daily_loss_limit: Decimal
) -> str:
    setup = SETUPS[setup_name]
    days = [d for d in recorded_days(settings.data_dir) if start <= d <= end]
    if not days:
        return "No recorded days in that range. Let the recorder run first."

    tuned_on, unseen = walk_forward_split(days, TRAIN_FRACTION)
    results: list[TradeResult] = []
    for day in unseen:
        results.extend(await run_day(settings, setup, day))

    summary = metrics(results)
    gate = evaluate_gate(summary, daily_loss_limit, Gate())
    wins = summary.wins
    honest = verdict(wins, summary.trades - wins, Decimal("2"))

    engine = make_engine(settings.database_url)
    async with make_sessions(engine)() as session, session.begin():
        session.add(
            Experiment(
                setup=setup_name,
                setup_version=getattr(setup, "version", "1"),
                data_from=unseen[0] if unseen else start,
                data_to=unseen[-1] if unseen else end,
                metrics=summary.model_dump(mode="json"),
            )
        )
    await engine.dispose()

    lines = [
        f"{setup_name}: tuned on {len(tuned_on)} days, tested on {len(unseen)} unseen days",
        f"Trades {summary.trades} · wins {summary.wins} · win rate {summary.win_rate:.1f}%",
        f"Net ₹{summary.net_pnl:.2f} · average ₹{summary.avg_net_pnl:.2f} a trade",
        f"Profit factor {summary.profit_factor} · worst streak {summary.worst_losing_streak}",
        honest,
        "Gate: PASSED" if gate.passed else "Gate: FAILED",
        *[f"  - {failure}" for failure in gate.failures],
    ]
    return "\n".join(lines)
