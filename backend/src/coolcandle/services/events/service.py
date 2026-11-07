"""The event calendar the guard reads, and a headline feed that only informs."""

import logging
from datetime import date

from sqlalchemy import delete, select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import Holiday, MarketEventRow
from coolcandle.domain.events import EventImpact, MarketEvent, MarketHoliday, tag_headline
from coolcandle.domain.models import Frozen
from coolcandle.ports.news_source import Headline, NewsSource

log = logging.getLogger(__name__)


class TaggedHeadline(Frozen):
    headline: Headline
    tags: tuple[str, ...]


def _to_event(row: MarketEventRow) -> MarketEvent:
    return MarketEvent(
        day=row.day,
        name=row.name,
        at=row.at,
        impact=EventImpact(row.impact),
        block_before_minutes=row.block_before_minutes,
        block_after_minutes=row.block_after_minutes,
    )


class EventService:
    def __init__(self, db: Sessions, sources: list[NewsSource]) -> None:
        self.db = db
        self.sources = sources
        self._days: dict[date, tuple[MarketEvent, ...]] = {}
        self.headlines: list[TaggedHeadline] = []

    def on(self, day: date) -> tuple[MarketEvent, ...]:
        """Events on a day from memory. `load` fills it; the guard reads this on the hot path."""
        return self._days.get(day, ())

    async def load(self, day: date) -> tuple[MarketEvent, ...]:
        async with self.db() as session:
            rows = await session.execute(select(MarketEventRow).where(MarketEventRow.day == day))
        self._days[day] = tuple(_to_event(row) for row in rows.scalars())
        return self._days[day]

    async def holidays(self, start: date, end: date) -> list[MarketHoliday]:
        """Exchange holidays from `start` to `end`, with the reason. A holiday on a weekend is
        listed too; an ordinary weekend is not."""
        async with self.db() as session:
            rows = await session.execute(
                select(Holiday).where(Holiday.date.between(start, end)).order_by(Holiday.date)
            )
        return [MarketHoliday(day=row.date, name=row.name) for row in rows.scalars()]

    async def upcoming(self, start: date) -> list[MarketEvent]:
        async with self.db() as session:
            rows = await session.execute(
                select(MarketEventRow)
                .where(MarketEventRow.day >= start)
                .order_by(MarketEventRow.day, MarketEventRow.at)
            )
        return [_to_event(row) for row in rows.scalars()]

    async def add(self, event: MarketEvent) -> None:
        async with self.db() as session, session.begin():
            session.add(
                MarketEventRow(
                    day=event.day,
                    name=event.name,
                    at=event.at,
                    impact=event.impact,
                    block_before_minutes=event.block_before_minutes,
                    block_after_minutes=event.block_after_minutes,
                )
            )
        await self.load(event.day)

    async def remove(self, day: date, name: str, today: date) -> bool:
        """Remove a future event. Today's calendar is frozen, like the plan it protects."""
        if day <= today:
            return False
        async with self.db() as session, session.begin():
            await session.execute(
                delete(MarketEventRow).where(MarketEventRow.day == day, MarketEventRow.name == name)
            )
        await self.load(day)
        return True

    async def refresh_headlines(self) -> None:
        fresh: list[TaggedHeadline] = []
        for source in self.sources:
            try:
                for headline in await source.latest():
                    fresh.append(
                        TaggedHeadline(headline=headline, tags=tag_headline(headline.title))
                    )
            except Exception:
                log.exception("a news source failed")
        self.headlines = fresh[:100]
