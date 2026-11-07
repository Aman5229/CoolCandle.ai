"""Alerts to the owner's own Telegram chat. Recommendations go to nobody else."""

import logging

import httpx2

from coolcandle.ports.notifier import Alert

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    async def send(self, alert: Alert) -> None:
        text = f"{alert.title}\n{alert.body}"
        if alert.link:
            text += f"\n{alert.link}"
        try:
            async with httpx2.AsyncClient(timeout=10) as http:
                response = await http.post(self._url, json={"chat_id": self.chat_id, "text": text})
            response.raise_for_status()
        except httpx2.HTTPError as exc:
            # The exception text would contain the URL, and so the bot token.
            log.warning("telegram alert failed: %s", type(exc).__name__)
