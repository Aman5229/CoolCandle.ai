from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import TakeSignalBody
from coolcandle.domain.models import OrderSource, Trade
from coolcandle.domain.signals import Signal

router = APIRouter()


@router.get("/signals")
async def today(request: Request, user: User) -> list[Signal]:
    signals: list[Signal] = await request.app.state.container.signals.today(user.account_id)
    return signals


@router.get("/signals/{signal_id}")
async def one(signal_id: str, request: Request, user: User, e: int, s: str) -> Signal:
    """The screen an alert links to. The link is signed and expires."""
    app = request.app.state.container
    if not app.notify.verify_link(signal_id, e, s, app.clock.now()):
        raise ApiError(410, "LINK_EXPIRED", "This alert has expired")
    signal: Signal = await app.signals.get(user.account_id, signal_id)
    return signal


@router.post("/signals/{signal_id}/take", dependencies=Protected)
async def take(signal_id: str, body: TakeSignalBody, request: Request, user: User) -> Trade:
    """This request IS the tap on screen: nothing else can enter a signal's trade (D6)."""
    trade: Trade = await request.app.state.container.signals.take(
        user.account_id, user.mode, signal_id, body.lots, OrderSource.SIGNAL
    )
    return trade


@router.post("/signals/{signal_id}/decline", dependencies=Protected)
async def decline(signal_id: str, request: Request, user: User) -> dict[str, bool]:
    await request.app.state.container.signals.decline(user.account_id, signal_id)
    return {"ok": True}
