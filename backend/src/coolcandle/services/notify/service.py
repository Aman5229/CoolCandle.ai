"""Alerts: always in-app, plus any notifier configured. Links are signed and expire."""

import hashlib
import hmac
import logging
from datetime import datetime

from coolcandle.ports.notifier import Alert, Notifier
from coolcandle.services.bus import EventBus

log = logging.getLogger(__name__)


class NotifyService:
    def __init__(
        self, bus: EventBus, notifiers: list[Notifier], secret: str, public_url: str
    ) -> None:
        self.bus = bus
        self.notifiers = notifiers
        self._secret = secret.encode()
        self.public_url = public_url.rstrip("/")

    async def alert(self, title: str, body: str, link: str | None = None) -> None:
        self.bus.publish("alert", {"title": title, "body": body, "link": link})
        for notifier in self.notifiers:
            try:
                await notifier.send(Alert(title=title, body=body, link=link))
            except Exception:
                log.exception("a notifier failed")

    def _signature(self, signal_id: str, expires: int) -> str:
        message = f"{signal_id}:{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:32]

    def signed_link(self, signal_id: str, expires_at: datetime) -> str:
        expires = int(expires_at.timestamp())
        signature = self._signature(signal_id, expires)
        return f"{self.public_url}/#/confirm/{signal_id}?e={expires}&s={signature}"

    def verify_link(self, signal_id: str, expires: int, signature: str, now: datetime) -> bool:
        good = hmac.compare_digest(self._signature(signal_id, expires), signature)
        return good and now.timestamp() <= expires
