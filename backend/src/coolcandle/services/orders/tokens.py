"""One-time confirm tokens. Only a tap in the UI creates one; signals and the buddy need it."""

import secrets
from datetime import datetime, timedelta

TOKEN_LIFE = timedelta(seconds=60)


class ConfirmTokens:
    def __init__(self) -> None:
        self._issued: dict[str, tuple[str, datetime]] = {}

    def issue(self, subject: str, now: datetime) -> str:
        token = secrets.token_urlsafe(16)
        self._issued[token] = (subject, now + TOKEN_LIFE)
        return token

    def consume(self, token: str | None, subject: str, now: datetime) -> bool:
        if token is None:
            return False
        issued = self._issued.pop(token, None)
        return issued is not None and issued[0] == subject and now <= issued[1]
