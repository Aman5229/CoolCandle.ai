from datetime import datetime
from typing import Any

from coolcandle.db.session import Sessions
from coolcandle.db.tables import AuditLog
from coolcandle.logs import redact


class Audit:
    def __init__(self, db: Sessions) -> None:
        self.db = db

    async def write(
        self,
        action: str,
        now: datetime,
        *,
        user_id: str | None = None,
        data: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        async with self.db() as session, session.begin():
            session.add(
                AuditLog(user_id=user_id, ts=now, action=action, data=redact(data or {}), ip=ip)
            )
