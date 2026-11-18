"""Skill Test over HTTP and the WebSocket, on the whole app, with index history from memory: CI
never calls Kotak. The replay is the real one, at 60x, paused or ended early to keep tests fast."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from coolcandle.api.ws import skill_event_for
from coolcandle.db.tables import User
from tests.api.conftest import Api
from tests.skill.conftest import DAY, seed

PLAN = {
    "profit_target": "10000",
    "loss_limit": "5000",
    "max_trades": 3,
    "max_loss_per_trade": "2500",
    "entry_start": "09:15",
    "entry_cutoff": "09:40",
    "square_off": "09:42",
}


def start(api: Api) -> dict[str, Any]:
    """A run on DAY and a NIFTY session, confirmed twice and then paused."""
    seed(api.container.skill.history)
    run = api.post("/api/skill/runs", {"start_fund": "100000", "start_day": DAY.isoformat()})
    assert run.status_code == 200, run.text
    created = api.post("/api/skill/sessions", {"index": "NIFTY", "speed": 60, "plan": PLAN})
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    assert api.post(f"/api/skill/sessions/{session_id}/confirm", {"step": 1}).status_code == 200
    started = api.post(f"/api/skill/sessions/{session_id}/confirm", {"step": 2, "typed": "START"})
    assert started.status_code == 200, started.text
    paused = api.post(f"/api/skill/sessions/{session_id}/replay", {"action": "pause"})
    assert paused.status_code == 200, paused.text
    result: dict[str, Any] = paused.json()
    return result


def test_skill_test_needs_a_session_and_the_csrf_token(api: Api) -> None:
    assert api.client.get("/api/skill/account").status_code == 401
    api.login()
    body = {"start_fund": "100000", "start_day": DAY.isoformat()}
    assert api.client.post("/api/skill/runs", json=body).status_code == 403


def test_without_history_the_lobby_says_so(api: Api) -> None:
    api.login()
    account = api.client.get("/api/skill/account").json()
    assert (account["indices"], account["first_day"], account["run"]) == ([], None, None)
    refused = api.post("/api/skill/runs", {"start_fund": "100000", "start_day": DAY.isoformat()})
    assert refused.json()["code"] == "NO_HISTORY"


def test_a_replayed_day_over_http_from_run_to_report(api: Api) -> None:
    api.login()
    session = start(api)
    account = api.client.get("/api/skill/account").json()
    assert account["indices"] == ["NIFTY"]
    assert account["run"]["today"] == DAY.isoformat()
    assert account["lot_sizes"] == {"NIFTY": 65, "SENSEX": 20}
    assert session["state"] == "ACTIVE"
    assert session["expiry"] == "2025-11-11"

    chain = api.client.get(f"/api/skill/sessions/{session['id']}/chain").json()
    assert chain["vol_source"] == "INDIA_VIX"
    atm = next(row for row in chain["rows"] if row["strike"] == chain["atm"])["ce"]
    ask = Decimal(atm["ask"])
    order = {
        "instrument_key": atm["key"],
        "lots": 1,
        "stop": str(ask - 20),
        "target": str(ask + 40),
    }
    preview = api.post(f"/api/skill/sessions/{session['id']}/preview", order).json()
    assert Decimal(preview["entry"]) == ask + Decimal("0.05")
    assert preview["decision"]["reasons"][0]["code"] == "PRICES_STALE"  # paused

    api.post(f"/api/skill/sessions/{session['id']}/replay", {"action": "play"})
    no_stop = api.post(f"/api/skill/sessions/{session['id']}/orders", order | {"stop": None})
    assert no_stop.status_code == 409
    assert no_stop.json()["code"] == "SKILL_BLOCKED"
    assert "NO_STOP" in [r["code"] for r in no_stop.json()["reasons"]]

    placed = api.post(f"/api/skill/sessions/{session['id']}/orders", order)
    assert placed.status_code == 200, placed.text
    assert placed.json()["trades"][0]["status"] == "OPEN"
    assert placed.json()["open_quote"]["key"] == atm["key"]
    early = api.client.get(f"/api/skill/sessions/{session['id']}/report")
    assert early.json()["code"] == "NOT_OVER"

    ended = api.post(f"/api/skill/sessions/{session['id']}/end")
    assert ended.json()["state"] == "ENDED"
    assert ended.json()["trades"][0]["exit_kind"] == "LOCK"
    report = api.client.get(f"/api/skill/sessions/{session['id']}/report")
    assert report.status_code == 200
    card = report.json()
    assert card["score"] == 95  # the refused attempt without a stop
    assert [line["line"] for line in card["lines"]] == ["BLOCKED"]
    account = api.client.get("/api/skill/account").json()
    assert account["run"]["today"] == "2025-11-11"  # the played day is spent
    assert account["recent"][0]["score"] == 95


def test_the_chart_comes_in_every_timeframe_and_never_past_the_cursor(api: Api) -> None:
    api.login()
    session = start(api)
    cursor = datetime.fromisoformat(session["replay"]["cursor"])
    for interval in ("1m", "5m", "15m", "1h", "1d"):
        response = api.client.get(
            f"/api/skill/sessions/{session['id']}/candles", params={"interval": interval}
        )
        assert response.status_code == 200, response.text
        candles = response.json()
        assert candles, interval
        assert {c["interval"] for c in candles} == {interval}
        assert all(datetime.fromisoformat(c["start"]) <= cursor for c in candles)
    minutes = api.client.get(f"/api/skill/sessions/{session['id']}/candles").json()
    assert all(datetime.fromisoformat(c["start"]) + timedelta(minutes=1) <= cursor for c in minutes)
    bad = api.client.get(f"/api/skill/sessions/{session['id']}/candles", params={"interval": "2m"})
    assert bad.status_code == 422


def test_websocket_sends_skill_events_only_to_their_owner(api: Api) -> None:
    api.login()

    async def own_id() -> str:
        async with api.container.db() as session:
            user_id: str = await session.scalar(select(User.id))
            return user_id

    user_id = api.client.portal.call(own_id)  # type: ignore[union-attr]
    bus = api.container.bus
    with api.client.websocket_connect("/ws") as socket:
        api.client.portal.call(  # type: ignore[union-attr]
            lambda: [
                bus.publish("skill.locked", {"user_id": "someone-else", "reason": "TARGET"}),
                bus.publish("skill.locked", {"user_id": user_id, "reason": "LOSS_LIMIT"}),
            ]
        )
        for _ in range(500):
            event = socket.receive_json()
            if event["type"].startswith("skill."):
                assert event == {
                    "type": "skill.locked",
                    "data": {"user_id": user_id, "reason": "LOSS_LIMIT"},
                }
                break
        else:
            raise AssertionError("the owner's event never arrived")


def test_skill_event_filter() -> None:
    assert skill_event_for({"user_id": "me"}, "me")
    assert not skill_event_for({"user_id": "you"}, "me")
    assert not skill_event_for({"user_id": None}, "me")
