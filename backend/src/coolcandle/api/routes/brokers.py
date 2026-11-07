from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import ConnectBody, CredentialsBody, FundsResponse
from coolcandle.ports.broker import BrokerSpec, BrokerStatus
from coolcandle.services.accounts.service import AccountView

router = APIRouter()


def _own(user: User, account_id: str) -> None:
    if account_id != user.account_id:
        raise ApiError(404, "NO_ACCOUNT", "No such account")


@router.get("/brokers")
async def catalog(request: Request, _: User) -> tuple[BrokerSpec, ...]:
    specs: tuple[BrokerSpec, ...] = request.app.state.container.catalog
    return specs


@router.get("/broker-accounts")
async def accounts(request: Request, user: User) -> list[AccountView]:
    return [await request.app.state.container.accounts.get(user.account_id)]


@router.post("/broker-accounts", dependencies=Protected)
async def save(body: CredentialsBody, request: Request, user: User) -> AccountView:
    app = request.app.state.container
    spec = next((s for s in app.catalog if s.id == body.broker), None)
    if spec is None or spec.status is not BrokerStatus.LIVE:
        raise ApiError(409, "BROKER_UNAVAILABLE", "That broker is not available yet")
    missing = [f.label for f in spec.fields if not body.credentials.get(f.name)]
    if missing:
        raise ApiError(422, "INVALID", f"Missing: {', '.join(missing)}")
    view: AccountView = await app.accounts.save_credentials(
        user.account_id, body.broker, body.label, body.credentials
    )
    return view


@router.delete("/broker-accounts/{account_id}", dependencies=Protected)
async def remove(account_id: str, request: Request, user: User) -> AccountView:
    _own(user, account_id)
    view: AccountView = await request.app.state.container.accounts.clear_credentials(account_id)
    return view


@router.post("/broker-accounts/{account_id}/connect", dependencies=Protected)
async def connect(
    account_id: str, body: ConnectBody, request: Request, user: User
) -> FundsResponse:
    _own(user, account_id)
    funds = await request.app.state.container.accounts.connect(account_id, body.totp)
    return FundsResponse(available=funds.available, used_margin=funds.used_margin)


@router.post("/broker-accounts/{account_id}/test", dependencies=Protected)
async def test(account_id: str, request: Request, user: User) -> FundsResponse:
    _own(user, account_id)
    funds = await request.app.state.container.accounts.test(account_id)
    return FundsResponse(available=funds.available, used_margin=funds.used_margin)
