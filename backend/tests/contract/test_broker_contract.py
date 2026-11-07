"""One suite every Broker adapter must pass. Kotak runs against a stand-in for its SDK:
nothing here ever talks to Kotak."""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coolcandle.adapters.brokers.kotak_neo.broker import KotakBroker
from coolcandle.adapters.brokers.kotak_neo.mapping import (
    check,
    order_from_kotak,
    position_from_kotak,
    rows,
)
from coolcandle.adapters.brokers.kotak_neo.scrips import (
    expiry_date,
    paise_to_rupees,
    parse_index_options,
)
from coolcandle.adapters.brokers.paper.broker import PaperBroker
from coolcandle.adapters.clock import ManualClock
from coolcandle.domain.models import (
    Intent,
    OptionType,
    OrderChanges,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    Tick,
)
from coolcandle.ports.broker import Broker, BrokerError, BrokerUnknownOutcome

NOW = datetime(2026, 11, 11, 4, 30, tzinfo=UTC)
KEY = "nse_fo|1001"
QUOTE = Tick(
    instrument_key=KEY,
    ts=NOW,
    received_at=NOW,
    ltp=Decimal("100"),
    bid=Decimal("105"),
    ask=Decimal("106"),
)


def buy(price: str = "100") -> OrderRequest:
    return OrderRequest(
        instrument_key=KEY,
        trading_symbol="NIFTY24500CE",
        exchange_segment="nse_fo",
        side=Side.BUY,
        qty=65,
        order_type=OrderType.LIMIT,
        price=Decimal(price),
        intent=Intent.ENTRY,
        trade_id="t1",
    )


# What Kotak sends for an empty order book or position list, seen on a real account.
NO_DATA = {"stat": "Not_Ok", "emsg": "No Data"}


class FakeNeo:
    """Answers the way the real SDK does: strings, and errors as data."""

    def __init__(self) -> None:
        self.book: dict[str, dict[str, Any]] = {}
        self.placed = 0

    def place_order(self, **kwargs: Any) -> dict[str, Any]:
        self.placed += 1
        order_id = str(1000 + self.placed)
        self.book[order_id] = {
            "nOrdNo": order_id,
            "ordSt": "open",
            "fldQty": "0",
            "avgPrc": "0.00",
            "trdSym": kwargs["trading_symbol"],
            "tok": "1001",
            "exSeg": kwargs["exchange_segment"],
            "trnsTp": kwargs["transaction_type"],
            "qty": kwargs["quantity"],
            "prcTp": kwargs["order_type"],
            "prc": kwargs["price"],
            "trgPrc": kwargs["trigger_price"],
            "GuiOrdId": kwargs["tag"],
        }
        return {"stat": "Ok", "nOrdNo": order_id}

    def modify_order(self, **kwargs: Any) -> dict[str, Any]:
        row = self.book.get(kwargs["order_id"])
        if row is None or row["ordSt"] != "open":
            return {"stat": "Not_Ok", "emsg": "Order is closed"}
        row.update(prc=kwargs["price"], qty=kwargs["quantity"], prcTp=kwargs["order_type"])
        return {"stat": "Ok", "nOrdNo": kwargs["order_id"]}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        row = self.book.get(order_id)
        if row is None or row["ordSt"] != "open":
            return {"stat": "Not_Ok", "emsg": "Order is closed"}
        row["ordSt"] = "cancelled"
        return {"stat": "Ok", "nOrdNo": order_id}

    def order_report(self) -> dict[str, Any]:
        if not self.book:
            return NO_DATA
        return {"stat": "Ok", "data": list(self.book.values())}

    def positions(self) -> dict[str, Any]:
        return NO_DATA

    def limits(self) -> dict[str, Any]:
        return {"stat": "Ok", "Net": "100000.50", "MarginUsed": "2500"}


def paper() -> Broker:
    return PaperBroker(ManualClock(NOW), lambda key: QUOTE, Decimal("500000"))


def kotak() -> Broker:
    broker = KotakBroker(ManualClock(NOW), login_fn=lambda *_: FakeNeo())
    broker.client = FakeNeo()
    return broker


@pytest.fixture(params=[paper, kotak], ids=["paper", "kotak"])
def broker(request: pytest.FixtureRequest) -> Broker:
    make: Callable[[], Broker] = request.param
    return make()


async def test_placing_returns_an_open_order_with_a_broker_id(broker: Broker) -> None:
    order = await broker.place_order(buy())
    assert order.broker_order_id
    assert order.status is OrderStatus.OPEN


async def test_a_placed_order_is_in_the_book(broker: Broker) -> None:
    order = await broker.place_order(buy())
    ids = [o.broker_order_id for o in await broker.orders()]
    assert order.broker_order_id in ids


async def test_an_order_can_be_modified(broker: Broker) -> None:
    order = await broker.place_order(buy())
    changed = await broker.modify_order(order.broker_order_id, OrderChanges(price=Decimal("99")))
    assert changed.request.price == Decimal("99")


async def test_an_order_can_be_cancelled(broker: Broker) -> None:
    order = await broker.place_order(buy())
    cancelled = await broker.cancel_order(order.broker_order_id)
    assert cancelled.status is OrderStatus.CANCELLED


async def test_cancelling_a_closed_order_is_refused(broker: Broker) -> None:
    order = await broker.place_order(buy())
    await broker.cancel_order(order.broker_order_id)
    with pytest.raises(BrokerError):
        await broker.cancel_order(order.broker_order_id)


async def test_modifying_an_unknown_order_is_refused(broker: Broker) -> None:
    with pytest.raises(BrokerError):
        await broker.modify_order("nope", OrderChanges(price=Decimal("1")))


async def test_funds_are_decimal(broker: Broker) -> None:
    funds = await broker.funds()
    assert isinstance(funds.available, Decimal)


async def test_a_changed_order_comes_out_of_the_updates_once(broker: Broker) -> None:
    order = await broker.place_order(buy())
    await broker.sync()
    broker.drain_updates()

    await broker.cancel_order(order.broker_order_id)
    await broker.sync()
    updates = broker.drain_updates()
    assert [u.status for u in updates if u.broker_order_id == order.broker_order_id] == [
        OrderStatus.CANCELLED
    ]
    await broker.sync()
    assert broker.drain_updates() == []


# --- Kotak specifics, all from its verified quirks ---------------------------


async def test_an_sdk_error_dict_on_place_is_an_unknown_outcome() -> None:
    broker = KotakBroker(ManualClock(NOW))

    class Timeout(FakeNeo):
        def place_order(self, **kwargs: Any) -> dict[str, Any]:
            return {"Error": "Read timed out"}

    broker.client = Timeout()
    with pytest.raises(BrokerUnknownOutcome):
        await broker.place_order(buy())


async def test_an_exception_on_place_is_an_unknown_outcome() -> None:
    broker = KotakBroker(ManualClock(NOW))

    class Dropped(FakeNeo):
        def place_order(self, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError

    broker.client = Dropped()
    with pytest.raises(BrokerUnknownOutcome):
        await broker.place_order(buy())


async def test_a_clear_refusal_is_a_broker_error_and_not_unknown() -> None:
    broker = KotakBroker(ManualClock(NOW))

    class Refuses(FakeNeo):
        def place_order(self, **kwargs: Any) -> dict[str, Any]:
            return {"stat": "Not_Ok", "emsg": "RMS: margin shortfall"}

    broker.client = Refuses()
    with pytest.raises(BrokerError) as refused:
        await broker.place_order(buy())
    assert not isinstance(refused.value, BrokerUnknownOutcome)
    assert "margin" in str(refused.value)


async def test_the_sdk_is_never_called_twice_for_one_order() -> None:
    broker = KotakBroker(ManualClock(NOW))
    fake = FakeNeo()
    broker.client = fake
    await broker.place_order(buy())
    assert fake.placed == 1


def test_every_kotak_number_becomes_a_decimal() -> None:
    order = order_from_kotak(
        {
            "nOrdNo": "1",
            "ordSt": "complete",
            "fldQty": "65",
            "avgPrc": "100.05",
            "trnsTp": "B",
            "qty": "65",
            "prcTp": "L",
            "prc": "100.00",
            "trgPrc": "0",
            "exSeg": "nse_fo",
            "tok": "1001",
            "trdSym": "X",
        },
        NOW,
    )
    assert order.status is OrderStatus.COMPLETE
    assert order.avg_price == Decimal("100.05")
    assert order.request.instrument_key == KEY


@pytest.mark.parametrize("status", ["validation pending", "open pending", "trigger pending"])
def test_anything_not_final_is_open(status: str) -> None:
    row = {"nOrdNo": "1", "ordSt": status, "qty": "65", "trnsTp": "S", "exSeg": "nse_fo"}
    assert order_from_kotak(row, NOW).status is OrderStatus.OPEN


def test_a_rejection_keeps_the_reason() -> None:
    row = {
        "nOrdNo": "1",
        "ordSt": "rejected",
        "rejRsn": "RMS",
        "qty": "65",
        "trnsTp": "B",
        "exSeg": "nse_fo",
    }
    order = order_from_kotak(row, NOW)
    assert order.reject_reason == "RMS"


def test_position_pnl_includes_the_price_factor() -> None:
    position = position_from_kotak(
        {
            "exSeg": "nse_fo",
            "tok": "1001",
            "flBuyQty": "65",
            "flSellQty": "65",
            "buyAmt": "6500.00",
            "sellAmt": "7000.00",
        }
    )
    assert position.net_qty == 0
    assert position.pnl(Decimal("0")) == Decimal("500.00")


def test_errors_inside_a_200_are_caught() -> None:
    with pytest.raises(BrokerError):
        check({"Error": "bad"}, "order")
    with pytest.raises(BrokerError):
        check({"stat": "Not_Ok", "emsg": "no"}, "order")


def test_no_data_is_an_empty_list_not_an_error() -> None:
    assert rows(NO_DATA, "order book") == []
    assert rows({"stat": "Not_Ok", "errMsg": " no data "}, "positions") == []
    with pytest.raises(BrokerError):
        rows({"stat": "Not_Ok", "emsg": "Session expired"}, "order book")


async def test_an_empty_kotak_account_reads_as_empty() -> None:
    broker = kotak()
    assert await broker.orders() == []
    assert await broker.positions() == []
    await broker.sync()


def test_expiry_counts_from_1980() -> None:
    # 2026-11-17 15:30 IST, in seconds since 1980-01-01.
    seconds = int(datetime(2026, 11, 17, 10, 0, tzinfo=UTC).timestamp()) - 315_532_800
    assert str(expiry_date(seconds)) == "2026-11-17"


def test_strikes_are_in_paise() -> None:
    assert paise_to_rupees("2450000") == Decimal("24500.00")


def test_scrip_columns_with_stray_characters_are_cleaned() -> None:
    lines = [
        "pSymbol ,pTrdSymbol,pSymbolName,pInstType,lExpiryDate,dStrikePrice;,pOptionType,"
        "lLotSize,dTickSize \n",
        "1001,NIFTY24500CE,NIFTY,OPTIDX,1478000000,2450000,CE,65,5\n",
        "1002,BANKNIFTY50000CE,BANKNIFTY,OPTIDX,1478000000,5000000,CE,30,5\n",
    ]
    options = parse_index_options(lines, "nse_fo", "NIFTY")
    assert len(options) == 1
    assert options[0].key == KEY
    assert options[0].strike == Decimal("24500.00")
    assert options[0].option_type is OptionType.CE
    assert options[0].tick_size == Decimal("0.05")


def test_the_sdk_can_never_log_the_mpin_or_write_its_own_log_file(tmp_path: Path) -> None:
    """The SDK logs call parameters at INFO, so a stray env var must not be able to turn it on."""
    probe = (
        "import logging, os\n"
        "import coolcandle.adapters.brokers.kotak_neo.client\n"
        "from neo_api_client import logger as sdk\n"
        "assert sdk.LOG_LEVEL == 'ERROR', sdk.LOG_LEVEL\n"
        "assert sdk.FILE_LOG_ENABLED is False\n"
    )
    env = os.environ | {"NEO_LOG_LEVEL": "DEBUG", "NEO_LOG_FILE_ENABLED": "true"}
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env, check=True)
    assert not (tmp_path / "logs").exists()
