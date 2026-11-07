"""Fresh tables and an admin for the browser test. Prints the authenticator secret."""

import asyncio
import sys

from sqlalchemy import create_engine

from coolcandle.config import Settings
from coolcandle.db.tables import Base
from coolcandle.main import build


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    path = settings.database_url.split("///")[1]
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    container = build(settings)
    print(await container.auth.create_admin(sys.argv[1], sys.argv[2]))


asyncio.run(main())
