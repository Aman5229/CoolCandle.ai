"""PaperBroker: fills against live quotes, with the same rules a real exchange applies."""

from collections.abc import Callable
from decimal import Decimal

from coolcandle.domain.models import (
    FINAL_ORDER_STATUSES,
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
from coolcandle.ports.broker import (
    BrokerError,
    BrokerSession,
    BrokerSpec,
    BrokerStatus,
)
from coolcandle.ports.clock import Clock

PAPER_SPEC = BrokerSpec(id="paper", name="Paper", status=BrokerStatus.LIVE)


class PaperBroker:
    spec = PAPER_SPEC

    def __init__(self, clock: Clock, quote: Callable[[str], Tick | None], cash: Decimal) -> None:
        self.clock = clock
        self.quote = quote
        self.cash = cash
        self._orders: dict[str, Order] = {}
        self._values: dict[str, Decimal] = {}  # filled quantity x price, per order
        self._triggered: set[str] = set()
        self._updates: list[Order] = []
        self._next_id = 1

    async def connect(self, creds: dict[str, str], totp: str) -> BrokerSession:
        return BrokerSession(connected_at=self.clock.now())

    def restore(self, orders: list[Order]) -> None:
        for order in orders:
            self._orders[order.broker_order_id] = order
            if order.filled_qty and order.avg_price is not None:
                self._values[order.broker_order_id] = order.avg_price * order.filled_qty
            number = order.broker_order_id.removeprefix("P")
            if number.isdigit():
                self._next_id = max(self._next_id, int(number) + 1)

    def _store(self, order: Order) -> Order:
        stamped = order.model_copy(update={"updated_at": self.clock.now()})
        self._orders[stamped.broker_order_id] = stamped
        self._updates.append(stamped)
        return stamped

    def drain_updates(self) -> list[Order]:
        updates, self._updates = self._updates, []
        return updates

    async def sync(self) -> None:
        return None

    async def place_order(self, req: OrderRequest) -> Order:
        if req.price is None:
            raise BrokerError("Paper orders need a price")
        broker_id = f"P{self._next_id}"
        self._next_id += 1
        tick = self.quote(req.instrument_key)
        status, reason = OrderStatus.OPEN, None
        if req.order_type is OrderType.SL and tick is not None:
            if req.trigger is None or req.trigger >= tick.ltp:
                status, reason = OrderStatus.REJECTED, "Trigger is above the last traded price"
        order = self._store(
            Order(
                id=broker_id,
                broker_order_id=broker_id,
                request=req,
                status=status,
                reject_reason=reason,
                updated_at=self.clock.now(),
            )
        )
        if tick is not None:
            self._match(broker_id, tick)
        return self._orders.get(broker_id, order)

    async def modify_order(self, broker_order_id: str, changes: OrderChanges) -> Order:
        order = self._live_order(broker_order_id)
        request = order.request.model_copy(
            update={
                "order_type": changes.order_type or order.request.order_type,
                "price": changes.price if changes.price is not None else order.request.price,
                "trigger": changes.trigger
                if changes.trigger is not None
                else order.request.trigger,
                "qty": changes.qty if changes.qty is not None else order.request.qty,
            }
        )
        if request.order_type is OrderType.LIMIT:
            request = request.model_copy(update={"trigger": None})
        if changes.trigger is not None:
            self._triggered.discard(broker_order_id)
        updated = self._store(order.model_copy(update={"request": request}))
        tick = self.quote(request.instrument_key)
        if tick is not None:
            self._match(broker_order_id, tick)
        return self._orders.get(broker_order_id, updated)

    async def cancel_order(self, broker_order_id: str) -> Order:
        order = self._live_order(broker_order_id)
        return self._store(order.model_copy(update={"status": OrderStatus.CANCELLED}))

    def _live_order(self, broker_order_id: str) -> Order:
        order = self._orders.get(broker_order_id)
        if order is None:
            raise BrokerError(f"No such order: {broker_order_id}")
        if order.status in FINAL_ORDER_STATUSES:
            raise BrokerError(f"Order {broker_order_id} is already {order.status}")
        return order

    def on_tick(self, tick: Tick) -> None:
        for order in list(self._orders.values()):
            if (
                order.status is OrderStatus.OPEN
                and order.request.instrument_key == tick.instrument_key
            ):
                self._match(order.broker_order_id, tick)

    def _fill_qty(self, order: Order) -> int:
        """All of what is left. Tests override this to produce partial fills."""
        return order.request.qty - order.filled_qty

    def _match(self, broker_id: str, tick: Tick) -> None:
        order = self._orders[broker_id]
        req = order.request
        if order.status is not OrderStatus.OPEN or req.price is None:
            return
        if req.order_type is OrderType.SL and broker_id not in self._triggered:
            if req.trigger is None or tick.ltp > req.trigger:
                return
            self._triggered.add(broker_id)

        bid = tick.bid if tick.bid is not None else tick.ltp
        ask = tick.ask if tick.ask is not None else tick.ltp
        if req.side is Side.BUY:
            if ask > req.price:
                return
            price = ask
        else:
            if bid < req.price:
                return
            price = bid

        qty = self._fill_qty(order)
        if qty <= 0:
            return
        value = self._values.get(broker_id, ZERO) + price * qty
        self._values[broker_id] = value
        filled = order.filled_qty + qty
        self._store(
            order.model_copy(
                update={
                    "filled_qty": filled,
                    "avg_price": value / filled,
                    "status": OrderStatus.COMPLETE if filled >= req.qty else OrderStatus.OPEN,
                }
            )
        )

    async def orders(self) -> list[Order]:
        return list(self._orders.values())

    async def positions(self) -> list[Position]:
        totals: dict[str, list[Decimal]] = {}
        for broker_id, order in self._orders.items():
            if order.filled_qty == 0:
                continue
            row = totals.setdefault(order.request.instrument_key, [ZERO, ZERO, ZERO, ZERO])
            value = self._values.get(broker_id, ZERO)
            if order.request.side is Side.BUY:
                row[0] += order.filled_qty
                row[2] += value
            else:
                row[1] += order.filled_qty
                row[3] += value
        return [
            Position(
                instrument_key=key,
                net_qty=int(row[0] - row[1]),
                buy_qty=int(row[0]),
                sell_qty=int(row[1]),
                buy_value=row[2],
                sell_value=row[3],
            )
            for key, row in totals.items()
        ]

    async def funds(self) -> Funds:
        spent = ZERO
        for position in await self.positions():
            spent += position.buy_value - position.sell_value
        return Funds(available=self.cash - spent, used_margin=spent, as_of=self.clock.now())

    async def quotes(self, keys: list[str]) -> list[Tick]:
        ticks = [self.quote(key) for key in keys]
        return [tick for tick in ticks if tick is not None]
