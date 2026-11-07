"""KotakBroker: real orders through the Kotak Neo SDK. The SDK is synchronous and never
retries, so calls run in a thread, and a failed place is checked, never repeated."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from coolcandle.adapters.brokers.kotak_neo.client import login
from coolcandle.adapters.brokers.kotak_neo.mapping import (
    ORDER_TYPES,
    check,
    order_from_kotak,
    position_from_kotak,
    rows,
    to_decimal,
)
from coolcandle.adapters.brokers.kotak_neo.spec import KOTAK_SPEC
from coolcandle.domain.models import (
    ZERO,
    Funds,
    Order,
    OrderChanges,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Tick,
)
from coolcandle.ports.broker import BrokerError, BrokerSession, BrokerUnknownOutcome
from coolcandle.ports.clock import Clock

log = logging.getLogger(__name__)

PRODUCT = "MIS"
VALIDITY = "DAY"
POSITIONS_TTL = timedelta(seconds=1.5)


class KotakBroker:
    spec = KOTAK_SPEC

    def __init__(
        self, clock: Clock, login_fn: Callable[[dict[str, str], str], Any] = login
    ) -> None:
        self.clock = clock
        self._login = login_fn
        self.client: Any = None
        self._known: dict[str, Order] = {}
        self._updates: list[Order] = []
        self._positions: list[Position] = []
        self._positions_at: datetime | None = None

    async def _call(self, method: str, /, **kwargs: Any) -> Any:
        if self.client is None:
            raise BrokerError("Kotak is not connected")
        return await asyncio.to_thread(getattr(self.client, method), **kwargs)

    async def connect(self, creds: dict[str, str], totp: str) -> BrokerSession:
        self.client = await asyncio.to_thread(self._login, creds, totp)
        return BrokerSession(connected_at=self.clock.now())

    def restore(self, orders: list[Order]) -> None:
        return None

    def on_tick(self, tick: Tick) -> None:
        return None

    def drain_updates(self) -> list[Order]:
        updates, self._updates = self._updates, []
        return updates

    async def orders(self) -> list[Order]:
        now = self.clock.now()
        book = rows(await self._call("order_report"), "order book")
        return [
            order_from_kotak(row, now) for row in book if str(row.get("exSeg", "")).endswith("_fo")
        ]

    async def sync(self) -> None:
        """Poll the order book and queue whatever changed since last time."""
        for order in await self.orders():
            seen = self._known.get(order.broker_order_id)
            changed = (
                seen is None
                or seen.status is not order.status
                or seen.filled_qty != order.filled_qty
            )
            self._known[order.broker_order_id] = order
            if changed:
                self._updates.append(order)

    async def positions(self) -> list[Position]:
        now = self.clock.now()
        if self._positions_at is not None and now - self._positions_at < POSITIONS_TTL:
            return self._positions
        book = rows(await self._call("positions"), "positions")
        self._positions = [
            position_from_kotak(row) for row in book if str(row.get("exSeg", "")).endswith("_fo")
        ]
        self._positions_at = now
        return self._positions

    async def funds(self) -> Funds:
        body = check(await self._call("limits"), "funds")
        return Funds(
            available=to_decimal(body.get("Net")) or ZERO,
            used_margin=to_decimal(body.get("MarginUsed")) or ZERO,
            as_of=self.clock.now(),
        )

    async def place_order(self, req: OrderRequest) -> Order:
        try:
            response = await self._call(
                "place_order",
                exchange_segment=req.exchange_segment,
                product=PRODUCT,
                price=str(req.price or 0),
                order_type=ORDER_TYPES[req.order_type],
                quantity=str(req.qty),
                validity=VALIDITY,
                trading_symbol=req.trading_symbol,
                transaction_type="B" if req.side is Side.BUY else "S",
                trigger_price=str(req.trigger) if req.trigger is not None else "0",
                tag=req.trade_id or "",
            )
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerUnknownOutcome(f"No clear answer from Kotak: {type(exc).__name__}") from exc

        if isinstance(response, dict) and ("Error" in response or "error" in response):
            # The SDK turns network failures into error dicts, so the order may still exist.
            raise BrokerUnknownOutcome("Kotak returned an error with no order number")
        body = check(response, "order")
        broker_id = body.get("nOrdNo")
        if not broker_id:
            raise BrokerUnknownOutcome("Kotak did not return an order number")
        order = Order(
            id=str(broker_id),
            broker_order_id=str(broker_id),
            request=req,
            status=OrderStatus.OPEN,
            updated_at=self.clock.now(),
        )
        self._known[order.broker_order_id] = order
        return order

    async def modify_order(self, broker_order_id: str, changes: OrderChanges) -> Order:
        current = self._known.get(broker_order_id)
        if current is None:
            raise BrokerError(f"Unknown order {broker_order_id}")
        req = current.request
        order_type = changes.order_type or req.order_type
        price = changes.price if changes.price is not None else req.price
        trigger = changes.trigger if changes.trigger is not None else req.trigger
        if order_type is OrderType.LIMIT:
            trigger = None
        qty = changes.qty if changes.qty is not None else req.qty
        check(
            await self._call(
                "modify_order",
                order_id=broker_order_id,
                price=str(price or 0),
                order_type=ORDER_TYPES[order_type],
                quantity=str(qty),
                validity=VALIDITY,
                trigger_price=str(trigger) if trigger is not None else "0",
            ),
            "modify",
        )
        updated = current.model_copy(
            update={
                "request": req.model_copy(
                    update={
                        "order_type": order_type,
                        "price": price,
                        "trigger": trigger,
                        "qty": qty,
                    }
                ),
                "updated_at": self.clock.now(),
            }
        )
        self._known[broker_order_id] = updated
        return updated

    async def cancel_order(self, broker_order_id: str) -> Order:
        check(await self._call("cancel_order", order_id=broker_order_id), "cancel")
        current = self._known[broker_order_id]
        return current.model_copy(update={"status": OrderStatus.CANCELLED})

    async def quotes(self, keys: list[str]) -> list[Tick]:
        return []
