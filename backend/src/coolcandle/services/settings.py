from typing import Any

from sqlalchemy import select

from coolcandle.db.session import Sessions
from coolcandle.db.tables import Setting


class SettingsStore:
    def __init__(self, db: Sessions) -> None:
        self.db = db

    async def all(self, user_id: str) -> dict[str, Any]:
        async with self.db() as session:
            rows = await session.execute(select(Setting).where(Setting.user_id == user_id))
        return {row.key: row.value for row in rows.scalars()}

    async def save(self, user_id: str, values: dict[str, Any]) -> None:
        async with self.db() as session, session.begin():
            rows = await session.execute(select(Setting).where(Setting.user_id == user_id))
            existing = {row.key: row for row in rows.scalars()}
            for key, value in values.items():
                if key in existing:
                    existing[key].value = value
                else:
                    session.add(Setting(user_id=user_id, key=key, value=value))
