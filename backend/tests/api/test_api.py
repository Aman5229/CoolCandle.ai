import logging
import time
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from coolcandle.db.tables import AuditLog, BrokerAccount, User
from coolcandle.main import create_app
from coolcandle.ports.agent import AgentAnswer, AgentRequest
from tests.api.conftest import EMAIL, PASSWORD, Api
from tests.conftest import make_settings


def wait_for(check: Any, seconds: float = 6.0) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def atm_call(api: Api) -> dict[str, Any]:
    def ready() -> dict[str, Any] | None:
        chain = api.client.get("/api/market/option-chain").json()
        rows = [r for r in chain["rows"] if r["strike"] == chain["atm"]]
        if rows and rows[0]["ce"] and rows[0]["ce"]["ask"]:
            result: dict[str, Any] = rows[0]["ce"]
            return result
        return None

    return wait_for(ready)  # type: ignore[no-any-return]


def ticket(call: dict[str, Any], **changes: Any) -> dict[str, Any]:
    ask = Decimal(call["ask"])
    body = {
        "instrument_key": call["key"],
        "lots": 1,
        "limit_price": str(ask),
        "stop_trigger": str((ask * Decimal("0.9")).quantize(Decimal("0.05"))),
    }
    return body | changes


def test_health_returns_ok(api: Api) -> None:
    response = api.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_requests_need_a_session(api: Api) -> None:
    response = api.client.get("/api/day/status")
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_SIGNED_IN"


def test_login_needs_the_password_and_the_code(api: Api) -> None:
    bad = {"email": EMAIL, "password": "wrong", "code": api.code()}
    assert api.client.post("/api/auth/login", json=bad).status_code == 401
    bad = {"email": EMAIL, "password": PASSWORD, "code": "000000"}
    assert api.client.post("/api/auth/login", json=bad).status_code == 401
    api.login()
    assert api.client.get("/api/auth/me").json()["email"] == EMAIL


def test_five_failures_lock_the_login(api: Api) -> None:
    bad = {"email": EMAIL, "password": "wrong", "code": "000000"}
    for _ in range(5):
        api.client.post("/api/auth/login", json=bad)
    good = {"email": EMAIL, "password": PASSWORD, "code": api.code()}
    response = api.client.post("/api/auth/login", json=good)
    assert response.json()["code"] == "LOCKED_OUT"


def test_the_session_cookie_is_http_only(api: Api) -> None:
    response = api.client.post(
        "/api/auth/login", json={"email": EMAIL, "password": PASSWORD, "code": api.code()}
    )
    cookies = response.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("cc_session"))
    assert "HttpOnly" in session
    assert "SameSite=strict" in session


def test_cookies_are_secure_by_default(tmp_path: Any) -> None:
    assert make_settings().cookie_secure is True


def test_writes_need_the_csrf_token(api: Api) -> None:
    api.login()
    response = api.client.post("/api/plan/today/confirm", json={"step": 1})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF"


def test_every_login_starts_in_paper(api: Api) -> None:
    api.login()
    assert api.client.get("/api/auth/me").json()["mode"] == "paper"


def test_there_is_no_sign_up_and_no_way_to_delete_a_lock(api: Api) -> None:
    paths = [getattr(r, "path", "") for r in api.client.app.routes]  # type: ignore[attr-defined]
    assert not any("signup" in p or "register" in p for p in paths)
    assert not any("lock" in p and "plan" not in p for p in paths)


def test_the_plan_takes_two_confirmations(api: Api) -> None:
    api.login()
    api.put(
        "/api/plan/today",
        {
            "profit_target": "5000",
            "loss_limit": "3000",
            "max_trades": 3,
            "max_loss_per_trade": "1500",
        },
    )
    assert api.post("/api/plan/today/confirm", {"step": 1}).json()["status"] == "CONFIRMING"
    wrong = api.post("/api/plan/today/confirm", {"step": 2, "typed": "ok"})
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "CONFIRM_WORD"
    final = api.post("/api/plan/today/confirm", {"step": 2, "typed": "LOCK"})
    assert final.json()["status"] == "LOCKED"


def test_a_bad_plan_gives_the_error_shape(api: Api) -> None:
    api.login()
    response = api.put(
        "/api/plan/today",
        {
            "profit_target": "5000",
            "loss_limit": "100",
            "max_trades": 3,
            "max_loss_per_trade": "500",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID"


def test_a_locked_plan_only_gets_stricter(api: Api) -> None:
    api.login()
    api.lock_plan()
    base = {
        "profit_target": "5000",
        "loss_limit": "3000",
        "max_trades": 3,
        "max_loss_per_trade": "1500",
    }
    looser = api.put("/api/plan/today", base | {"loss_limit": "4000"})
    assert looser.status_code == 409
    assert looser.json()["code"] == "PLAN_LOCKED"
    stricter = api.put("/api/plan/today", base | {"loss_limit": "2000"})
    assert stricter.status_code == 200
    assert stricter.json()["status"] == "LOCKED"
    assert Decimal(stricter.json()["loss_limit"]) == 2000


def test_money_travels_as_strings(api: Api) -> None:
    api.login()
    api.lock_plan()
    status = api.client.get("/api/day/status").json()
    assert isinstance(status["snapshot"]["net_pnl"], str)
    assert isinstance(status["plan"]["loss_limit"], str)


def test_live_needs_the_typed_word_a_broker_and_a_real_feed(api: Api) -> None:
    api.login()
    assert api.post("/api/mode", {"mode": "live"}).json()["code"] == "CONFIRM_WORD"
    response = api.post("/api/mode", {"mode": "live", "typed": "LIVE"})
    assert response.json()["code"] == "BROKER_SESSION"

    class Connected:
        pass

    api.container.brokers.set_live(api.client.get("/api/auth/me").json()["account_id"], Connected())
    response = api.post("/api/mode", {"mode": "live", "typed": "LIVE"})
    assert response.json()["code"] == "SIMULATED_FEED"


def test_the_catalog_only_offers_kotak(api: Api) -> None:
    api.login()
    catalog = api.client.get("/api/brokers").json()
    live = [b["id"] for b in catalog if b["status"] == "live"]
    assert live == ["kotak_neo"]
    assert len(catalog) == 5


def test_a_coming_soon_broker_cannot_be_saved(api: Api) -> None:
    api.login()
    response = api.post(
        "/api/broker-accounts",
        {"broker": "zerodha", "credentials": {"api_key": "x", "api_secret": "y"}},
    )
    assert response.json()["code"] == "BROKER_UNAVAILABLE"


SECRETS = {
    "consumer_key": "CK-super-secret",
    "mobile_number": "+919999999999",
    "ucc": "ABC12",
    "mpin": "246810",
}


def test_credentials_are_encrypted_and_never_come_back(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    api.login()
    response = api.post("/api/broker-accounts", {"broker": "kotak_neo", "credentials": SECRETS})
    assert response.status_code == 200
    assert response.json()["has_credentials"] is True
    listed = api.client.get("/api/broker-accounts").text

    async def stored() -> tuple[str, str]:
        async with api.container.db() as session:
            account = (await session.execute(select(BrokerAccount))).scalar_one()
            audit = (await session.execute(select(AuditLog))).scalars().all()
        return account.credentials_enc, str([row.data for row in audit])

    encrypted, audit = api.client.portal.call(stored)  # type: ignore[union-attr]
    for value in ("CK-super-secret", "246810"):
        assert value not in response.text
        assert value not in listed
        assert value not in encrypted
        assert value not in audit
        assert value not in caplog.text


def test_a_failed_login_never_logs_the_password(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bad = {"email": EMAIL, "password": "my-secret-guess", "code": "123456"}
    api.client.post("/api/auth/login", json=bad)
    assert "my-secret-guess" not in caplog.text


def test_a_validation_error_never_echoes_the_input(api: Api) -> None:
    response = api.client.post("/api/auth/login", json={"email": EMAIL, "password": "pw-secret"})
    assert response.status_code == 422
    assert "pw-secret" not in response.text


def test_the_totp_secret_is_stored_encrypted(api: Api) -> None:
    async def stored() -> str:
        async with api.container.db() as session:
            user = (await session.execute(select(User))).scalar_one()
            return str(user.totp_secret_enc)

    assert api.secret not in api.client.portal.call(stored)  # type: ignore[union-attr]


def test_a_paper_trade_end_to_end_on_the_simulator(api: Api) -> None:
    api.login()
    api.lock_plan()
    call = atm_call(api)

    preview = api.post("/api/orders/preview", ticket(call))
    assert preview.status_code == 200
    assert preview.json()["decision"]["action"] in ("ALLOW", "WARN")
    assert preview.json()["size"]["lots"] >= 1

    fresh = atm_call(api)
    placed = api.post("/api/trades", ticket(fresh))
    assert placed.status_code == 200, placed.text
    trade_id = placed.json()["id"]

    def is_open() -> bool:
        trades = api.client.get("/api/trades").json()
        return bool(trades[0]["state"] == "OPEN")

    wait_for(is_open)
    assert api.post(f"/api/trades/{trade_id}/exit").status_code == 200

    def is_closed() -> bool:
        trades = api.client.get("/api/trades").json()
        return bool(trades[0]["state"] == "CLOSED")

    wait_for(is_closed)
    timeline = api.client.get(f"/api/trades/{trade_id}").json()
    assert timeline[-1]["type"] == "EXIT_FILLED"
    assert api.client.get("/api/day/status").json()["snapshot"]["trades_taken"] == 1


def test_a_blocked_order_says_why(api: Api) -> None:
    api.login()
    call = atm_call(api)
    response = api.post("/api/trades", ticket(call))
    assert response.status_code == 409
    assert response.json()["code"] == "NO_PLAN"
    assert response.json()["reasons"][0]["code"] == "NO_PLAN"


def test_the_buddy_says_when_it_is_not_set_up(api: Api) -> None:
    api.login()
    reply = api.post("/api/agent/chat", {"message": "hello"}).json()
    assert "not set up" in reply["text"]


def test_agent_tools_need_the_token_of_a_chat_in_progress(api: Api) -> None:
    path = "/internal/agent/tools/get_plan"
    assert api.client.post(path, json={}).status_code == 401
    made_up = api.client.post(path, json={}, headers={"Authorization": "Bearer made-up"})
    assert made_up.status_code == 401
    assert made_up.json()["code"] == "TOOL_TOKEN"
    assert api.client.post("/api" + path, json={}).status_code == 404


def test_the_agent_reaches_core_tools_over_http_only_during_its_chat(api: Api) -> None:
    api.login()
    api.lock_plan()
    app = api.client.app
    seen: dict[str, Any] = {}

    class LoopbackAgent:
        """Calls core's tools the way the agent service does, over HTTP with the tool token."""

        async def chat(self, request: AgentRequest) -> AgentAnswer:
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://core") as http:
                response = await http.post(
                    "/internal/agent/tools/get_plan",
                    json={},
                    headers={"Authorization": f"Bearer {request.tool_token}"},
                )
            seen["during"] = response.status_code, response.json()
            seen["token"] = request.tool_token
            return AgentAnswer(text="Your loss limit is 3000.")

    api.container.agent.agent = LoopbackAgent()
    reply = api.post("/api/agent/chat", {"message": "My limit?"}).json()
    assert reply == {"text": "Your loss limit is 3000.", "proposal": None}
    status, plan = seen["during"]
    assert status == 200
    assert plan["loss_limit"] == "3000.00"
    after = api.client.post(
        "/internal/agent/tools/get_plan",
        json={},
        headers={"Authorization": f"Bearer {seen['token']}"},
    )
    assert after.status_code == 401


def test_the_holidays_of_a_month_come_with_reasons(api: Api) -> None:
    api.login()
    response = api.client.get(
        "/api/market/holidays", params={"start": "2026-11-01", "end": "2026-11-30"}
    )
    assert response.json() == [
        {"day": "2026-11-08", "name": "Diwali Laxmi Pujan (Muhurat trading)"},
        {"day": "2026-11-10", "name": "Diwali-Balipratipada"},
        {"day": "2026-11-24", "name": "Prakash Gurpurb Sri Guru Nanak Dev"},
    ]


def test_holidays_need_a_sensible_range(api: Api) -> None:
    api.login()
    backwards = api.client.get(
        "/api/market/holidays", params={"start": "2026-12-01", "end": "2026-11-01"}
    )
    assert backwards.status_code == 422
    assert (
        api.client.get("/api/market/holidays", params={"start": "x", "end": "y"}).status_code == 422
    )


def test_todays_events_show_and_future_ones_can_be_added(api: Api) -> None:
    api.login()
    events = api.client.get("/api/market/events").json()
    assert any(e["name"] == "RBI policy decision" for e in events)
    added = api.post("/api/market/events", {"day": "2027-02-01", "name": "Union Budget"})
    assert added.status_code == 200


def test_settings_round_trip(api: Api) -> None:
    api.login()
    api.put("/api/settings", {"values": {"theme": "light"}})
    assert api.client.get("/api/settings").json()["theme"] == "light"


def test_sign_out_everywhere(api: Api) -> None:
    api.login()
    assert api.post("/api/settings/sign-out-everywhere").json() == {"ok": True}
    assert api.client.get("/api/auth/me").status_code == 401


def test_the_websocket_needs_a_session(api: Api) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), api.client.websocket_connect("/ws") as socket:
        socket.receive_json()


def test_the_websocket_pushes_the_day_status(api: Api) -> None:
    api.login()
    with api.client.websocket_connect("/ws") as socket:
        for _ in range(200):
            event = socket.receive_json()
            if event["type"] == "day_status":
                assert event["data"]["status"]["state"] == "NO_PLAN"
                break
        else:
            raise AssertionError("no day_status arrived")


def test_the_app_builds_without_touching_the_database() -> None:
    assert isinstance(create_app().title, str)
    assert TestClient(create_app()).get("/api/health").status_code == 200


def test_a_locked_day_reports_when_it_locked_and_when_it_ends(api: Api) -> None:
    api.login()
    api.lock_plan(max_trades=1)
    placed = api.post("/api/trades", ticket(atm_call(api)))
    assert placed.status_code == 200, placed.text
    trade_id = placed.json()["id"]
    wait_for(lambda: api.client.get("/api/trades").json()[0]["state"] == "OPEN")
    api.post(f"/api/trades/{trade_id}/exit")

    def locked() -> dict[str, Any] | None:
        day: dict[str, Any] = api.client.get("/api/day/status").json()
        return day if day["locked_at"] else None

    day = wait_for(locked)
    assert day["locked_at"] is not None
    assert day["lock_ends_at"] is not None
    assert day["status"]["lock_reason"] == "MAX_TRADES"


def test_simulated_prices_never_reach_the_recordings(api: Api) -> None:
    api.login()
    atm_call(api)
    assert api.container.engine.recorder is None
    assert not (api.container.settings.data_dir / "ticks").exists()


def test_kotak_mode_starts_with_no_prices_at_all(tmp_path: Any) -> None:
    from coolcandle.main import default_market

    settings = make_settings(market_source="kotak", data_dir=tmp_path)
    market = default_market(settings, None)  # type: ignore[arg-type]
    assert not market.is_real
    assert type(market).__name__ == "IdleMarket"
