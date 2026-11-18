"""What Skill Test needs from the outside: stored index minutes, a replayable day, its own broker.

`main.py` wires these to the Parquet history store, the candle replay and a fresh PaperBroker per
session. Skill Test is never handed a live broker: it only ever gets what `SkillBrokerFactory`
builds."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from coolcandle.domain.models import Candle, Instrument, Tick
from coolcandle.domain.skill.models import StopSide
from coolcandle.ports.broker import Broker
from coolcandle.ports.clock import Clock
from coolcandle.ports.market_data import MarketData


class IndexData(Protocol):
    def days(self, index: str) -> list[date]: ...
    def window(self, index: str, start: datetime, end: datetime) -> list[Candle]: ...
    def bars(self, index: str, interval: str, start: datetime, end: datetime) -> list[Candle]: ...


class Replay(MarketData, Protocol):
    """One past day played through its own clock. Nothing after `cursor` is ever shown."""

    @property
    def clock(self) -> Clock: ...
    @property
    def cursor(self) -> datetime: ...
    @property
    def speed(self) -> int: ...
    @property
    def paused(self) -> bool: ...
    @property
    def finished(self) -> bool: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def set_speed(self, speed: int) -> None: ...
    def played(self) -> list[Candle]: ...


class ReplayFactory(Protocol):
    def __call__(
        self,
        instrument: Instrument,
        candles: list[Candle],
        start: datetime,
        *,
        speed: int,
        paused: bool,
        stop_side: Callable[[Candle], StopSide | None],
    ) -> Replay: ...


class SkillBrokerFactory(Protocol):
    """A fresh paper broker for one session, filling against that session's prices only."""

    def __call__(
        self, clock: Clock, quote: Callable[[str], Tick | None], slippage: Decimal
    ) -> Broker: ...
