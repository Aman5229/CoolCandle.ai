"""The whole app, for real: FastAPI, the simulator, the background loops and a database file."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from coolcandle.db.tables import Base
from coolcandle.main import create_app
from tests.conftest import make_settings

EMAIL = "aman@example.com"
PASSWORD = "a-long-test-password"


@dataclass
class Api:
    client: TestClient
    secret: str

    @property
    def container(self) -> Any:
        return self.client.app.state.container  # type: ignore[attr-defined]

    def code(self) -> str:
        return pyotp.TOTP(self.secret).now()

    def login(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": PASSWORD, "code": self.code()},
        )
        assert response.status_code == 200, response.text

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.client.cookies.get("cc_csrf") or ""}

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(path, json=body or {}, headers=self.headers())

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.put(path, json=body, headers=self.headers())

    def lock_plan(self, **changes: Any) -> None:
        body = {
            "profit_target": "5000",
            "loss_limit": "3000",
            "max_trades": 3,
            "max_loss_per_trade": "1500",
        }
        assert self.put("/api/plan/today", body | changes).status_code == 200
        assert self.post("/api/plan/today/confirm", {"step": 1}).status_code == 200
        locked = self.post("/api/plan/today/confirm", {"step": 2, "typed": "LOCK"})
        assert locked.status_code == 200


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Api]:
    path = tmp_path / "api.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    settings = make_settings(
        database_url=f"sqlite+aiosqlite:///{path}",
        cookie_secure=False,
        data_dir=tmp_path / "data",
        sim_speed=10.0,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        container = app.state.container
        secret = client.portal.call(container.auth.create_admin, EMAIL, PASSWORD)  # type: ignore[union-attr]
        yield Api(client=client, secret=secret)
