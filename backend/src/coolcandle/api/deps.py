"""Request dependencies: the container, the signed-in user, and the CSRF check."""

import hmac
from typing import Annotated, Protocol

from fastapi import Cookie, Depends, Header, Request

from coolcandle.api.errors import ApiError
from coolcandle.services.auth.service import AuthService, CurrentUser

SESSION_COOKIE = "cc_session"
CSRF_COOKIE = "cc_csrf"


class HasAuth(Protocol):
    auth: AuthService


def container(request: Request) -> object:
    return request.app.state.container


async def current_user(
    request: Request, cc_session: Annotated[str | None, Cookie()] = None
) -> CurrentUser:
    if cc_session is None:
        raise ApiError(401, "NOT_SIGNED_IN", "Please sign in")
    user = await request.app.state.container.auth.current(cc_session)
    if user is None:
        raise ApiError(401, "NOT_SIGNED_IN", "Please sign in")
    return user  # type: ignore[no-any-return]


def csrf(
    cc_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """The double-submit check: only our own page can read the cookie to echo it back."""
    if cc_csrf is None or x_csrf_token is None or not hmac.compare_digest(cc_csrf, x_csrf_token):
        raise ApiError(403, "CSRF", "Reload the page and try again")


User = Annotated[CurrentUser, Depends(current_user)]
Protected = [Depends(csrf)]
