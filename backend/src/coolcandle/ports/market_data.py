from collections.abc import AsyncIterator
from datetime import date
from typing import Protocol

from coolcandle.domain.models import Candle, Instrument, Tick


class MarketData(Protocol):
    is_real: bool  # False for simulated or replayed prices; live orders refuse those

    async def instruments(self) -> list[Instrument]: ...
    async def subscribe(self, keys: list[str]) -> None: ...
    def ticks(self) -> AsyncIterator[Tick]: ...
    async def history(self, key: str, interval: str, start: date, end: date) -> list[Candle]: ...
