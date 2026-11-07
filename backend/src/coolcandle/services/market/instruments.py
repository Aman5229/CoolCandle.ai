"""Today's tradable instruments, from the data source's scrip list."""

from datetime import date
from decimal import Decimal

from coolcandle.domain.models import Instrument, InstrumentKind, OptionType


class InstrumentBook:
    def __init__(self) -> None:
        self._by_key: dict[str, Instrument] = {}

    def load(self, instruments: list[Instrument]) -> None:
        self._by_key = {i.key: i for i in instruments}

    def all(self) -> list[Instrument]:
        return list(self._by_key.values())

    def get(self, key: str) -> Instrument | None:
        return self._by_key.get(key)

    def index_key(self, underlying: str) -> str | None:
        for instrument in self._by_key.values():
            if instrument.kind is InstrumentKind.INDEX and instrument.underlying == underlying:
                return instrument.key
        return None

    def indices(self) -> list[Instrument]:
        return [i for i in self._by_key.values() if i.kind is InstrumentKind.INDEX]

    def options(self, underlying: str, expiry: date | None = None) -> list[Instrument]:
        return [
            i
            for i in self._by_key.values()
            if i.kind is InstrumentKind.OPTION
            and i.underlying == underlying
            and (expiry is None or i.expiry == expiry)
        ]

    def nearest_expiry(self, underlying: str, today: date) -> date | None:
        expiries = [
            i.expiry for i in self.options(underlying) if i.expiry is not None and i.expiry >= today
        ]
        return min(expiries, default=None)

    def atm_option(
        self, underlying: str, spot: Decimal, side: OptionType, today: date
    ) -> Instrument | None:
        expiry = self.nearest_expiry(underlying, today)
        candidates = [o for o in self.options(underlying, expiry) if o.option_type is side]
        if not candidates:
            return None
        return min(candidates, key=lambda o: abs((o.strike or Decimal(0)) - spot))
