"""Every error leaves as { "code": ..., "message": ... }."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from coolcandle.domain.exits import ExitPlanError
from coolcandle.domain.plan import PlanError
from coolcandle.services.accounts.service import AccountError
from coolcandle.services.auth.service import AuthError
from coolcandle.services.orders.routing import BrokerUnavailable
from coolcandle.services.orders.service import OrderBlocked, TradeError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _body(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, **extra})


def add_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _body(exc.status, exc.code, exc.message)

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return _body(401, exc.code, exc.message)

    @app.exception_handler(PlanError)
    async def plan_error(_: Request, exc: PlanError) -> JSONResponse:
        return _body(409, exc.code, exc.message)

    @app.exception_handler(ExitPlanError)
    async def exit_error(_: Request, exc: ExitPlanError) -> JSONResponse:
        return _body(422, exc.code, exc.message)

    @app.exception_handler(AccountError)
    async def account_error(_: Request, exc: AccountError) -> JSONResponse:
        return _body(409, exc.code, exc.message)

    @app.exception_handler(BrokerUnavailable)
    async def broker_error(_: Request, exc: BrokerUnavailable) -> JSONResponse:
        return _body(409, "BROKER_SESSION", str(exc))

    @app.exception_handler(OrderBlocked)
    async def blocked(_: Request, exc: OrderBlocked) -> JSONResponse:
        reasons = [r.model_dump() for r in exc.decision.reasons]
        return _body(409, exc.code, exc.message, reasons=reasons)

    @app.exception_handler(TradeError)
    async def trade_error(_: Request, exc: TradeError) -> JSONResponse:
        return _body(409, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # The errors echo the input, which may be a password, so keep only the field and the reason.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())[1:])
        return _body(422, "INVALID", f"{field}: {first.get('msg', 'invalid')}".strip(": "))

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, exc: ValueError) -> JSONResponse:
        return _body(422, "INVALID", str(exc))
