"""The Broker port. OrderService is the only caller of its order methods."""

from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime

from coolcandle.domain.models import (
    Frozen,
    Funds,
    Order,
    OrderChanges,
    OrderRequest,
    Position,
    Tick,
)


class BrokerError(Exception):
    """The broker definitely refused. Its words are safe to show the user."""


class BrokerUnknownOutcome(BrokerError):
    """No clear answer, so the order may exist. Check the order book; never retry blindly."""


class BrokerStatus(StrEnum):
    LIVE = "live"
    COMING_SOON = "coming_soon"


class FieldSpec(Frozen):
    name: str
    label: str
    type: str = "text"
    secret: bool = False


class BrokerSpec(Frozen):
    id: str
    name: str
    status: BrokerStatus
    fields: tuple[FieldSpec, ...] = ()
    daily_fields: tuple[FieldSpec, ...] = ()


class BrokerSession(Frozen):
    connected_at: AwareDatetime


class Broker(Protocol):
    spec: BrokerSpec

    async def connect(self, creds: dict[str, str], totp: str) -> BrokerSession: ...
    async def funds(self) -> Funds: ...
    async def positions(self) -> list[Position]: ...
    async def orders(self) -> list[Order]: ...
    async def place_order(self, req: OrderRequest) -> Order: ...
    async def modify_order(self, broker_order_id: str, changes: OrderChanges) -> Order: ...
    async def cancel_order(self, broker_order_id: str) -> Order: ...
    async def sync(self) -> None:
        """Refresh from the broker. New order states come out of `drain_updates`."""
        ...

    def drain_updates(self) -> list[Order]: ...
    def on_tick(self, tick: Tick) -> None: ...
    def restore(self, orders: list[Order]) -> None:
        """Rebuild state after a restart, for brokers that live in memory."""
        ...

    async def quotes(self, keys: list[str]) -> list[Tick]: ...
