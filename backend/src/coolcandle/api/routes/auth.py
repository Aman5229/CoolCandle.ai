import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response

from coolcandle.api.deps import CSRF_COOKIE, SESSION_COOKIE, Protected, User
from coolcandle.api.schemas import LoginBody, MeResponse

router = APIRouter(prefix="/auth")
SESSION_SECONDS = 12 * 3600


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
    app = request.app.state.container
    client = request.client.host if request.client else None
    token = await app.auth.login(
        body.email, body.password, body.code, client, request.headers.get("user-agent")
    )
    secure = app.settings.cookie_secure
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(24),
        max_age=SESSION_SECONDS,
        secure=secure,
        samesite="strict",
    )
    await app.engine.refresh_accounts()
    return {"ok": True}


@router.post("/logout", dependencies=Protected)
async def logout(
    request: Request,
    response: Response,
    cc_session: Annotated[str | None, Cookie()] = None,
) -> dict[str, bool]:
    if cc_session is not None:
        await request.app.state.container.auth.logout(cc_session)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: User, request: Request) -> MeResponse:
    app = request.app.state.container
    return MeResponse(
        email=user.email,
        mode=user.mode,
        account_id=user.account_id,
        is_simulated=app.settings.market_source == "sim",
    )
