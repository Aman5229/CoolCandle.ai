from typing import Any

from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.schemas import SettingsBody

router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request, user: User) -> dict[str, Any]:
    values: dict[str, Any] = await request.app.state.container.settings_store.all(user.id)
    return values


@router.put("/settings", dependencies=Protected)
async def put_settings(body: SettingsBody, request: Request, user: User) -> dict[str, Any]:
    store = request.app.state.container.settings_store
    await store.save(user.id, body.values)
    values: dict[str, Any] = await store.all(user.id)
    return values


@router.get("/settings/sessions")
async def sessions(request: Request, user: User) -> list[dict[str, Any]]:
    rows = await request.app.state.container.auth.sessions(user.id)
    return [
        {"id": r.id, "created_at": r.created_at, "ip": r.ip, "user_agent": r.user_agent}
        for r in rows
    ]


@router.post("/settings/sign-out-everywhere", dependencies=Protected)
async def sign_out_everywhere(request: Request, user: User) -> dict[str, bool]:
    await request.app.state.container.auth.logout_everywhere(user.id)
    return {"ok": True}
