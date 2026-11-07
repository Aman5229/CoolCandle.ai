"""Which Broker serves an account in a mode. Adapters come in through factories."""

from collections.abc import Callable

from coolcandle.domain.models import Mode
from coolcandle.ports.broker import Broker


class BrokerUnavailable(Exception):
    pass


class BrokerRouter:
    def __init__(self, paper_factory: Callable[[str], Broker]) -> None:
        self._paper_factory = paper_factory
        self._paper: dict[str, Broker] = {}
        self._live: dict[str, Broker] = {}

    def paper(self, account_id: str) -> Broker:
        if account_id not in self._paper:
            self._paper[account_id] = self._paper_factory(account_id)
        return self._paper[account_id]

    def reset_paper(self) -> None:
        """A new trading day starts flat."""
        self._paper.clear()

    def set_live(self, account_id: str, broker: Broker) -> None:
        self._live[account_id] = broker

    def clear_live(self, account_id: str) -> None:
        self._live.pop(account_id, None)

    def has_live(self, account_id: str) -> bool:
        return account_id in self._live

    def get(self, account_id: str, mode: Mode) -> Broker:
        if mode is Mode.PAPER:
            return self.paper(account_id)
        broker = self._live.get(account_id)
        if broker is None:
            raise BrokerUnavailable("Connect the broker first")
        return broker

    def all(self) -> list[tuple[str, Mode, Broker]]:
        pairs = [(a, Mode.PAPER, b) for a, b in self._paper.items()]
        return pairs + [(a, Mode.LIVE, b) for a, b in self._live.items()]
