"""The option chain around the money, built from live quotes."""

from datetime import date
from decimal import Decimal

from coolcandle.domain.models import Frozen, Instrument, OptionType
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook


class ChainSide(Frozen):
    key: str
    trading_symbol: str
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    oi: int | None


class ChainRow(Frozen):
    strike: Decimal
    ce: ChainSide | None
    pe: ChainSide | None


class OptionChain(Frozen):
    underlying: str
    spot: Decimal | None
    expiry: date | None
    atm: Decimal | None
    lot_size: int | None
    rows: tuple[ChainRow, ...]


def _side(option: Instrument | None, quotes: QuoteBook) -> ChainSide | None:
    if option is None:
        return None
    tick = quotes.get(option.key)
    return ChainSide(
        key=option.key,
        trading_symbol=option.trading_symbol,
        ltp=tick.ltp if tick else None,
        bid=tick.bid if tick else None,
        ask=tick.ask if tick else None,
        oi=tick.oi if tick else None,
    )


def build_chain(
    underlying: str, today: date, book: InstrumentBook, quotes: QuoteBook, around: int = 10
) -> OptionChain:
    index_key = book.index_key(underlying)
    spot_tick = quotes.get(index_key) if index_key else None
    spot = spot_tick.ltp if spot_tick else None
    expiry = book.nearest_expiry(underlying, today)
    options = book.options(underlying, expiry)
    strikes = sorted({o.strike for o in options if o.strike is not None})
    if not strikes:
        return OptionChain(
            underlying=underlying, spot=spot, expiry=expiry, atm=None, lot_size=None, rows=()
        )
    centre = spot if spot is not None else strikes[len(strikes) // 2]
    atm = min(strikes, key=lambda s: abs(s - centre))
    middle = strikes.index(atm)
    shown = strikes[max(middle - around, 0) : middle + around + 1]
    by_slot = {(o.strike, o.option_type): o for o in options}
    rows = tuple(
        ChainRow(
            strike=strike,
            ce=_side(by_slot.get((strike, OptionType.CE)), quotes),
            pe=_side(by_slot.get((strike, OptionType.PE)), quotes),
        )
        for strike in shown
    )
    return OptionChain(
        underlying=underlying,
        spot=spot,
        expiry=expiry,
        atm=atm,
        lot_size=options[0].lot_size,
        rows=rows,
    )
