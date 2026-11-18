"""Skill Test on the real services: an in-memory database, index history from memory, and a gate
that decides exactly how many replayed ticks play. Nothing calls Kotak."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from coolcandle.adapters.clock import ManualClock
from coolcandle.adapters.market.history.replay import CandleReplay
from coolcandle.domain.calendar import ist_datetime, to_ist
from coolcandle.domain.models import Candle, Instrument, OptionType
from coolcandle.domain.skill.models import SessionPlan, StopSide
from coolcandle.services.skill.service import NewSession, SkillService, SkillTicket
from coolcandle.services.skill.views import OptionQuote, RunView, SessionView
from tests.conftest import FakeIndexData, Harness, make_harness

DAY = date(2025, 11, 10)  # a Monday: NIFTY's weekly expiry is Tuesday 11 November
NEXT_DAY = date(2025, 11, 11)
MINUTE = timedelta(minutes=1)
SPOT = Decimal("25000")


def minute(day: date, at: time, o: str, h: str, low: str, c: str, index: str = "NIFTY") -> Candle:
    return Candle(
        instrument_key=f"skill|{index}",
        interval="1m",
        start=ist_datetime(day, at),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def flat(day: date, start: time, count: int, price: str, index: str = "NIFTY") -> list[Candle]:
    first = ist_datetime(day, start)
    return [
        minute(day, to_ist(first + i * MINUTE).time(), price, price, price, price, index)
        for i in range(count)
    ]


def at(hour: int, minute_: int) -> time:
    return time(hour, minute_)


def seed(history: FakeIndexData, session_day: list[Candle] | None = None) -> None:
    """Forty quiet weekdays before DAY (one minute each), DAY itself, and NEXT_DAY. India VIX sits
    at 14 all through DAY, so the model's volatility is 0.14."""
    earlier: list[Candle] = []
    day = DAY
    while len(earlier) < 40:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            earlier.append(minute(day, at(15, 29), "24990", "25000", "24980", "24990"))
    history.add("NIFTY", earlier)
    history.add("NIFTY", session_day or flat(DAY, at(9, 15), 30, "25000"))
    history.add("NIFTY", flat(NEXT_DAY, at(9, 15), 30, "25100"))
    history.add("INDIAVIX", flat(DAY, at(9, 15), 375, "14"))


def plan(**changes: Any) -> SessionPlan:
    values: dict[str, Any] = {
        "profit_target": "10000",
        "loss_limit": "5000",
        "max_trades": 3,
        "max_loss_per_trade": "2500",
        "entry_start": "09:15",
        "entry_cutoff": "09:40",
        "square_off": "09:42",
    }
    return SessionPlan.model_validate(values | changes)


async def until(check: Callable[[], bool], seconds: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not check():
        if loop.time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.002)


class Gate:
    """Stands in for the replay's sleep, so a test says exactly how many ticks play. With
    `per_minute=4` each minute is four ticks: open, high, low, close (or low before high)."""

    def __init__(self) -> None:
        self.permits = asyncio.Semaphore(0)
        self.parked = 0  # how many times the replay has finished a tick and waited

    async def sleep(self, _seconds: float) -> None:
        self.parked += 1
        await self.permits.acquire()

    async def ready(self) -> None:
        await until(lambda: self.parked >= 1)

    async def step(self, ticks: int, done: Callable[[], bool] = lambda: False) -> None:
        target = self.parked + ticks
        for _ in range(ticks):
            self.permits.release()
        await until(lambda: self.parked >= target or done())


def gated(gate: Gate) -> Any:
    def factory(
        instrument: Instrument,
        candles: list[Candle],
        start: datetime,
        *,
        speed: int,
        paused: bool,
        stop_side: Callable[[Candle], StopSide | None],
    ) -> CandleReplay:
        return CandleReplay(
            instrument,
            candles,
            start,
            ManualClock(start),
            speed=speed,
            paused=paused,
            per_minute=4,
            stop_side=stop_side,
            sleep=gate.sleep,
        )

    return factory


@dataclass
class Skill:
    h: Harness
    gate: Gate

    @property
    def service(self) -> SkillService:
        return self.h.app.skill

    @property
    def user(self) -> str:
        return self.h.user_id

    @property
    def history(self) -> FakeIndexData:
        return self.service.history  # type: ignore[return-value]

    async def run(self, fund: str = "100000", day: date = DAY) -> RunView:
        return await self.service.start_run(self.user, Decimal(fund), day, None)

    async def start(self, index: str = "NIFTY", **changes: Any) -> SessionView:
        """A session on the run's next day, confirmed twice, with its first tick played."""
        draft = await self.service.create_session(
            self.user, NewSession(index=index, plan=plan(**changes))
        )
        await self.service.confirm(self.user, draft.id, 1, None)
        view = await self.service.confirm(self.user, draft.id, 2, "START")
        await self.gate.ready()
        return view

    async def option(self, session_id: str, kind: OptionType = OptionType.CE) -> OptionQuote:
        """The option at the money, at its model price now."""
        chain = await self.service.chain(self.user, session_id)
        row = next(r for r in chain.rows if r.strike == chain.atm)
        return row.ce if kind is OptionType.CE else row.pe

    async def buy(self, session_id: str, quote: OptionQuote, **changes: Any) -> SessionView:
        ask = quote.ask
        values: dict[str, Any] = {
            "instrument_key": quote.key,
            "lots": 1,
            "stop": ask - 20,
            "target": ask + 40,
        }
        ticket = SkillTicket.model_validate(values | changes)
        return await self.service.enter(self.user, session_id, ticket)

    async def view(self, session_id: str) -> SessionView:
        return await self.service.view(self.user, session_id)


@pytest.fixture
async def skill() -> AsyncIterator[Skill]:
    built = await make_harness()
    gate = Gate()
    built.app.skill.replays = gated(gate)
    seed(built.app.skill.history)  # type: ignore[arg-type]
    yield Skill(built, gate)
    await built.app.skill.stop()
    await built.app.db.kw["bind"].dispose()
