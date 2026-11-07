"""Which instruments to watch: both indices, plus strikes around the money, re-centred."""

from datetime import date
from decimal import Decimal

from coolcandle.domain.models import Instrument
from coolcandle.ports.market_data import MarketData
from coolcandle.services.market.instruments import InstrumentBook

STRIKES_EACH_SIDE = 10
RECENTRE_AFTER_STRIKES = 2


class FeedManager:
    def __init__(self, instruments: InstrumentBook) -> None:
        self.instruments = instruments
        self.market: MarketData | None = None
        self._centres: dict[str, Decimal] = {}

    async def use(self, market: MarketData) -> None:
        self.market = market
        self._centres.clear()
        self.instruments.load(await market.instruments())
        await market.subscribe([i.key for i in self.instruments.indices()])

    def _around(self, underlying: str, spot: Decimal, today: date) -> list[Instrument]:
        expiry = self.instruments.nearest_expiry(underlying, today)
        options = self.instruments.options(underlying, expiry)
        strikes = sorted({o.strike for o in options if o.strike is not None})
        if not strikes:
            return []
        centre = min(strikes, key=lambda s: abs(s - spot))
        middle = strikes.index(centre)
        wanted = set(strikes[max(middle - STRIKES_EACH_SIDE, 0) : middle + STRIKES_EACH_SIDE + 1])
        return [o for o in options if o.strike in wanted]

    def _strike_step(self, underlying: str) -> Decimal:
        strikes = sorted({o.strike for o in self.instruments.options(underlying) if o.strike})
        return strikes[1] - strikes[0] if len(strikes) > 1 else Decimal(50)

    async def on_index_tick(self, underlying: str, spot: Decimal, today: date) -> None:
        """Subscribe around the money the first time, then each time spot moves 2 strikes."""
        if self.market is None:
            return
        centre = self._centres.get(underlying)
        step = self._strike_step(underlying)
        if centre is not None and abs(spot - centre) < RECENTRE_AFTER_STRIKES * step:
            return
        self._centres[underlying] = spot
        await self.market.subscribe([o.key for o in self._around(underlying, spot, today)])
