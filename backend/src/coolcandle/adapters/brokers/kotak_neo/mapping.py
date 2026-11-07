"""The only place that knows Kotak's field names. Every value arrives as text or float."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from coolcandle.domain.models import (
    Intent,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Tick,
)
from coolcandle.ports.broker import BrokerError

ORDER_TYPES = {OrderType.LIMIT: "L", OrderType.SL: "SL", OrderType.SL_M: "SL-M"}
FINAL = {
    "complete": OrderStatus.COMPLETE,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
}
INDEX_NAMES = {"Nifty 50": "nse_cm|Nifty 50", "SENSEX": "bse_cm|SENSEX", "Sensex": "bse_cm|SENSEX"}


def to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def to_int(value: Any) -> int:
    number = to_decimal(value)
    return int(number) if number is not None else 0


def brief(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 300 else text[:300] + "…"


def check(response: Any, what: str) -> dict[str, Any]:
    """Refusals come back as data, so check every response."""
    if not isinstance(response, dict):
        raise BrokerError(f"Unexpected {what} response from Kotak: {brief(response)}")
    for key in ("Error", "error", "Error Message"):
        if key in response:
            raise BrokerError(f"Kotak {what} failed: {brief(response[key])}")
    stat = str(response.get("stat", "")).lower()
    if stat and stat != "ok":
        raise BrokerError(str(response.get("emsg") or response.get("errMsg") or brief(response)))
    return response


def rows(response: Any, what: str) -> list[dict[str, Any]]:
    # An empty order book or position list comes back as a refusal with the message "No Data".
    if isinstance(response, dict):
        message = response.get("emsg") or response.get("errMsg") or ""
        if str(message).strip().lower() == "no data":
            return []
    body = check(response, what)
    data = body.get("data")
    if data is None:  # an empty book comes back without data
        return []
    if not isinstance(data, list):
        raise BrokerError(f"Unexpected {what} from Kotak: {brief(body)}")
    return data


def order_from_kotak(row: dict[str, Any], now: datetime) -> Order:
    status_text = str(row.get("ordSt", "")).strip().lower()
    filled = to_int(row.get("fldQty"))
    avg = to_decimal(row.get("avgPrc"))
    side = Side.BUY if row.get("trnsTp") == "B" else Side.SELL
    order_type = OrderType.SL if row.get("prcTp") in ("SL", "SL-M") else OrderType.LIMIT
    tag = str(row.get("GuiOrdId") or "") or None
    broker_id = str(row["nOrdNo"])
    return Order(
        id=broker_id,
        broker_order_id=broker_id,
        request=OrderRequest(
            instrument_key=f"{row.get('exSeg', '')}|{row.get('tok', '')}",
            trading_symbol=str(row.get("trdSym", "")),
            exchange_segment=str(row.get("exSeg", "")),
            side=side,
            qty=max(to_int(row.get("qty")), 1),
            order_type=order_type,
            price=to_decimal(row.get("prc")),
            trigger=to_decimal(row.get("trgPrc")),
            intent=Intent.ENTRY if side is Side.BUY else Intent.EXIT,
            trade_id=tag,
        ),
        # Anything not final (open, trigger pending, validation pending...) is still open.
        status=FINAL.get(status_text, OrderStatus.OPEN),
        filled_qty=filled,
        avg_price=avg if filled and avg else None,
        reject_reason=str(row.get("rejRsn") or "rejected") if status_text == "rejected" else None,
        updated_at=now,
    )


def position_from_kotak(row: dict[str, Any]) -> Position:
    factor = (
        (to_decimal(row.get("multiplier")) or Decimal(1))
        * (to_decimal(row.get("genNum")) or Decimal(1))
        / (to_decimal(row.get("genDen")) or Decimal(1))
        * (to_decimal(row.get("prcNum")) or Decimal(1))
        / (to_decimal(row.get("prcDen")) or Decimal(1))
    )
    buy_qty = to_int(row.get("flBuyQty")) + to_int(row.get("cfBuyQty"))
    sell_qty = to_int(row.get("flSellQty")) + to_int(row.get("cfSellQty"))

    def amount(key: str) -> Decimal:
        return to_decimal(row.get(key)) or Decimal(0)

    return Position(
        instrument_key=f"{row.get('exSeg', '')}|{row.get('tok', '')}",
        net_qty=buy_qty - sell_qty,
        buy_qty=buy_qty,
        sell_qty=sell_qty,
        buy_value=(amount("buyAmt") + amount("cfBuyAmt")) * factor,
        sell_value=(amount("sellAmt") + amount("cfSellAmt")) * factor,
    )


def _dec(value: float) -> Decimal:
    # Via str(): Decimal(145.2) would keep the float's binary noise.
    return Decimal(str(value))


def _when(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def tick_from_message(message: Any, now: datetime) -> Tick | None:
    """A Tick from a feed message, or None for market-status and unknown messages."""
    kind = type(message).__name__
    if kind == "SFeedIndex":
        key = INDEX_NAMES.get(str(getattr(message, "name", "")))
        if key is None:
            return None
        return Tick(
            instrument_key=key,
            ts=_when(message.last_trade_time),
            received_at=now,
            ltp=_dec(message.last_traded_price),
        )
    if kind == "SFeedScrip":
        best_bid = message.buy[0].price if message.buy else 0
        best_ask = message.sell[0].price if message.sell else 0
        return Tick(
            instrument_key=f"{message.exchange_segment}|{message.instrument_token}",
            ts=_when(message.last_trade_time),
            received_at=now,
            ltp=_dec(message.last_traded_price),
            bid=_dec(best_bid) if best_bid > 0 else None,
            ask=_dec(best_ask) if best_ask > 0 else None,
            volume_today=message.volume_traded_today,
            oi=message.open_interest,
        )
    return None
