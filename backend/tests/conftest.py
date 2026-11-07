"""Shared harness: the real services on a fake clock, an in-memory database and paper fills."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic_settings import SettingsConfigDict

os.environ.setdefault("COOLCANDLE_SECRET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("COOLCANDLE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from coolcandle.adapters.clock import ManualClock  # noqa: E402
from coolcandle.config import Settings  # noqa: E402
from coolcandle.db.tables import Base, BrokerAccount, User  # noqa: E402
from coolcandle.domain.calendar import ist_datetime  # noqa: E402
from coolcandle.domain.models import (  # noqa: E402
    Candle,
    EntryTicket,
    Instrument,
    InstrumentKind,
    Mode,
    OptionType,
    Tick,
    Trade,
)
from coolcandle.domain.plan import DailyPlan  # noqa: E402
from coolcandle.main import Container, build  # noqa: E402

DAY = date(2026, 11, 11)  # a Wednesday, and a trading day
NIFTY = "nse_cm|Nifty 50"
CALL = "nse_fo|1001"
PUT = "nse_fo|1002"
LOT = 65


class IsolatedSettings(Settings):
    """Ignores the developer's backend/.env, so tests run the same on every machine."""

    model_config = SettingsConfigDict(env_file=None)


def make_settings(**values: Any) -> Settings:
    return IsolatedSettings(**values)


def instrument_list() -> list[Instrument]:
    def option(key: str, side: OptionType) -> Instrument:
        return Instrument(
            key=key,
            exchange_segment="nse_fo",
            trading_symbol=f"NIFTY24500{side}",
            kind=InstrumentKind.OPTION,
            underlying="NIFTY",
            expiry=date(2026, 11, 17),
            strike=Decimal("24500"),
            option_type=side,
            lot_size=LOT,
            tick_size=Decimal("0.05"),
            freeze_qty=LOT * 27,
        )

    index = Instrument(
        key=NIFTY,
        exchange_segment="nse_cm",
        trading_symbol="NIFTY",
        kind=InstrumentKind.INDEX,
        underlying="NIFTY",
    )
    return [index, option(CALL, OptionType.CE), option(PUT, OptionType.PE)]


class FakeMarket:
    is_real = False

    async def instruments(self) -> list[Instrument]:
        return instrument_list()

    async def subscribe(self, keys: list[str]) -> None:
        return None

    async def ticks(self) -> Any:
        return
        yield

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        return []


def plan_values(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "profit_target": Decimal("5000"),
        "loss_limit": Decimal("3000"),
        "max_trades": 3,
        "max_loss_per_trade": Decimal("1500"),
    }
    return values | changes


@dataclass
class Harness:
    app: Container
    clock: ManualClock
    account_id: str
    mode: Mode = Mode.PAPER

    async def lock_plan(self, **changes: Any) -> DailyPlan:
        await self.app.plans.save_draft(self.account_id, plan_values(**changes))
        await self.app.plans.confirm(self.account_id, 1, None)
        return await self.app.plans.confirm(self.account_id, 2, "LOCK")

    async def tick(
        self,
        key: str,
        ltp: str,
        bid: str | None = None,
        ask: str | None = None,
    ) -> None:
        price = Decimal(ltp)
        now = self.clock.now()
        await self.app.engine.on_tick(
            Tick(
                instrument_key=key,
                ts=now,
                received_at=now,
                ltp=price,
                bid=Decimal(bid) if bid else price,
                ask=Decimal(ask) if ask else price,
            )
        )
        await self.app.engine.pump()

    async def settle(self) -> None:
        """Let fills, timers and the monitor all run once."""
        await self.app.engine.pump()
        await self.app.orders.housekeeping()
        await self.app.engine.pump()
        await self.app.engine.check_all()
        await self.app.engine.pump()

    def ticket(self, **changes: Any) -> EntryTicket:
        values: dict[str, Any] = {
            "instrument_key": CALL,
            "lots": 1,
            "limit_price": Decimal("100"),
            "stop_trigger": Decimal("90"),
        }
        return EntryTicket(**(values | changes))

    async def enter(self, **changes: Any) -> Trade:
        await self.tick(NIFTY, "24500")
        await self.tick(CALL, "100", "99.95", "100")
        trade = await self.app.orders.submit_entry(
            self.account_id, self.mode, self.ticket(**changes)
        )
        await self.app.engine.pump()
        current = self.app.book.get(trade.id)
        return current if current is not None else trade

    def active(self) -> list[Trade]:
        return self.app.book.active(self.account_id, self.mode)

    async def trades(self) -> list[Trade]:
        return await self.app.journal.trades(self.account_id, self.mode, DAY)


TEST_DATABASE = os.environ.get("COOLCANDLE_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


async def make_harness(database_url: str = TEST_DATABASE) -> Harness:
    settings = make_settings(database_url=database_url)
    clock = ManualClock(ist_datetime(DAY, time(10, 0)))
    app = build(settings, clock=clock, market=FakeMarket())
    app.engine.recorder = None
    async with app.db.kw["bind"].begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with app.db() as session, session.begin():
        user = User(email="aman@example.com", password_hash="-", totp_secret_enc="-")
        session.add(user)
        await session.flush()
        account = BrokerAccount(user_id=user.id)
        session.add(account)
        await session.flush()
        account_id = account.id
    await app.engine.feed.use(FakeMarket())
    app.engine._indices = {NIFTY: "NIFTY"}
    await app.engine.refresh_accounts()
    return Harness(app=app, clock=clock, account_id=account_id)


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    built = await make_harness()
    yield built
    await built.app.db.kw["bind"].dispose()


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return ist_datetime(DAY, time(hour, minute, second))


NOW = datetime(2026, 11, 11, 4, 30, tzinfo=UTC)  # 10:00 IST


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--agent-evals", action="store_true", help="Run evals on the real model")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_model: spends real money on the model API")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--agent-evals"):
        return
    skip = pytest.mark.skip(reason="needs --agent-evals and an API key")
    for item in items:
        if "real_model" in item.keywords:
            item.add_marker(skip)
