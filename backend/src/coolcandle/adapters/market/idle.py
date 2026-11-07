"""IdleMarket: no prices at all, for the time before the broker's real feed connects."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date

from coolcandle.domain.models import Candle, Instrument, Tick


class IdleMarket:
    is_real = False

    async def instruments(self) -> list[Instrument]:
        return []

    async def subscribe(self, keys: list[str]) -> None:
        return None

    async def ticks(self) -> AsyncIterator[Tick]:
        await asyncio.Event().wait()
        nothing: list[Tick] = []
        for tick in nothing:
            yield tick

    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]:
        return []
