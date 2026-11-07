"""Broker accounts: encrypted credentials and the daily connect. The account row is permanent,
so removing credentials can never wipe a plan or a lock."""

from collections.abc import Awaitable, Callable

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import BrokerAccount
from coolcandle.domain.models import Frozen, Funds, Mode
from coolcandle.ports.broker import Broker, BrokerError
from coolcandle.ports.clock import Clock
from coolcandle.services.accounts.vault import Vault
from coolcandle.services.audit import Audit
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.routing import BrokerRouter


class AccountError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AccountView(Frozen):
    id: str
    broker: str
    label: str
    status: str
    has_credentials: bool


class AccountService:
    def __init__(
        self,
        *,
        db: Sessions,
        clock: Clock,
        vault: Vault,
        audit: Audit,
        brokers: BrokerRouter,
        book: TradeBook,
        live_factory: Callable[[str], Broker],
        on_connected: Callable[[str, Broker], Awaitable[None]],
    ) -> None:
        self.db = db
        self.clock = clock
        self.vault = vault
        self.audit = audit
        self.brokers = brokers
        self.book = book
        self.live_factory = live_factory
        self.on_connected = on_connected

    def _view(self, row: BrokerAccount) -> AccountView:
        status = "connected" if self.brokers.has_live(row.id) else row.status
        return AccountView(
            id=row.id,
            broker=row.broker,
            label=row.label,
            status=status,
            has_credentials=row.credentials_enc is not None,
        )

    async def get(self, account_id: str) -> AccountView:
        async with self.db() as session:
            row = await session.get(BrokerAccount, account_id)
        if row is None:
            raise AccountError("NO_ACCOUNT", "No such account")
        return self._view(row)

    async def all_ids(self) -> list[str]:
        async with self.db() as session:
            return list((await session.execute(select(BrokerAccount.id))).scalars())

    async def save_credentials(
        self, account_id: str, broker: str, label: str, credentials: dict[str, str]
    ) -> AccountView:
        async with self.db() as session, session.begin():
            row = await session.get(BrokerAccount, account_id)
            if row is None:
                raise AccountError("NO_ACCOUNT", "No such account")
            row.broker = broker
            row.label = label
            row.credentials_enc = self.vault.encrypt_dict(credentials)
            row.status = "saved"
        await self.audit.write("broker_saved", self.clock.now(), data={"broker": broker})
        return self._view(row)

    async def clear_credentials(self, account_id: str) -> AccountView:
        """Remove credentials. Refused while a live trade is open."""
        if self.book.active(account_id, Mode.LIVE):
            raise AccountError("OPEN_TRADE", "Close the open trade first")
        async with self.db() as session, session.begin():
            row = await session.get(BrokerAccount, account_id)
            if row is None:
                raise AccountError("NO_ACCOUNT", "No such account")
            row.broker = "paper"
            row.label = "Paper"
            row.credentials_enc = None
            row.status = "not_connected"
        self.brokers.clear_live(account_id)
        await self.audit.write("broker_removed", self.clock.now())
        return self._view(row)

    async def connect(self, account_id: str, totp: str) -> Funds:
        """The daily connect. The TOTP is used once and never stored."""
        async with self.db() as session, session.begin():
            row = await session.get(BrokerAccount, account_id)
            if row is None or row.credentials_enc is None:
                raise AccountError("NO_CREDENTIALS", "Save the broker's credentials first")
            credentials = self.vault.decrypt_dict(row.credentials_enc)
            broker = self.live_factory(row.broker)
            try:
                await broker.connect(credentials, totp)
            except BrokerError as exc:
                raise AccountError("CONNECT_FAILED", str(exc)) from exc
            row.status = "connected"
            row.last_connected_at = self.clock.now()
        self.brokers.set_live(account_id, broker)
        await self.on_connected(account_id, broker)
        await self.audit.write("broker_connected", self.clock.now())
        return await broker.funds()

    async def test(self, account_id: str) -> Funds:
        try:
            return await self.brokers.get(account_id, Mode.LIVE).funds()
        except Exception as exc:
            raise AccountError("TEST_FAILED", "The broker session is not working") from exc
