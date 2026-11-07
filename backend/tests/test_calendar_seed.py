"""Startup makes the stored holidays match the official list, fixing earlier mistakes."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import select

from coolcandle import seeds
from coolcandle.db.tables import Holiday
from coolcandle.main import seed_calendar
from tests.conftest import Harness, make_harness


@pytest.fixture
async def built() -> AsyncIterator[Harness]:
    harness = await make_harness()
    yield harness
    await harness.app.db.kw["bind"].dispose()


async def stored(harness: Harness) -> dict[date, str]:
    async with harness.app.db() as session:
        rows = (await session.execute(select(Holiday))).scalars()
        return {row.date: row.name for row in rows}


async def test_a_wrong_holiday_is_removed_and_a_wrong_name_corrected(built: Harness) -> None:
    async with built.app.db() as session, session.begin():
        session.add(Holiday(date=date(2026, 11, 9), exchange="NSE", name="Diwali Balipratipada"))
        session.add(Holiday(date=date(2026, 11, 10), exchange="NSE", name="Diwali"))
        session.add(Holiday(date=date(2027, 3, 22), exchange="NSE", name="Holi (a guess)"))

    await seed_calendar(built.app)

    assert await stored(built) == dict(seeds.HOLIDAYS)
    assert date(2026, 11, 9) not in built.app.calendar.holidays
    assert built.app.calendar.is_trading_day(date(2026, 11, 9))
    assert not built.app.calendar.is_trading_day(date(2026, 11, 10))


async def test_running_it_twice_changes_nothing(built: Harness) -> None:
    await seed_calendar(built.app)
    await seed_calendar(built.app)
    assert await stored(built) == dict(seeds.HOLIDAYS)


async def test_holidays_come_with_their_reason(built: Harness) -> None:
    await seed_calendar(built.app)
    november = await built.app.events.holidays(date(2026, 11, 1), date(2026, 11, 30))
    assert [(h.day, h.name) for h in november] == [
        (date(2026, 11, 8), "Diwali Laxmi Pujan (Muhurat trading)"),
        (date(2026, 11, 10), "Diwali-Balipratipada"),
        (date(2026, 11, 24), "Prakash Gurpurb Sri Guru Nanak Dev"),
    ]
