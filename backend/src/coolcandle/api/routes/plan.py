from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import ConfirmBody, DayResponse, ModeBody, PlanBody
from coolcandle.domain.models import Mode
from coolcandle.domain.plan import DailyPlan

router = APIRouter()
LIVE_WORD = "LIVE"


@router.get("/plan/today")
async def get_plan(request: Request, user: User) -> DailyPlan | None:
    plan: DailyPlan | None = await request.app.state.container.plans.get(user.account_id)
    return plan


@router.put("/plan/today", dependencies=Protected)
async def put_plan(body: PlanBody, request: Request, user: User) -> DailyPlan:
    plan: DailyPlan = await request.app.state.container.plans.save_draft(
        user.account_id, body.model_dump()
    )
    return plan


@router.post("/plan/today/confirm", dependencies=Protected)
async def confirm(body: ConfirmBody, request: Request, user: User) -> DailyPlan:
    plan: DailyPlan = await request.app.state.container.plans.confirm(
        user.account_id, body.step, body.typed
    )
    return plan


@router.post("/plan/today/cancel", dependencies=Protected)
async def cancel(request: Request, user: User) -> DailyPlan:
    plan: DailyPlan = await request.app.state.container.plans.cancel(user.account_id)
    return plan


@router.get("/day/status")
async def day_status(request: Request, user: User) -> DayResponse:
    app = request.app.state.container
    view = await app.guard.view(user.account_id, user.mode)
    now = app.clock.now()
    return DayResponse(
        trading_date=app.calendar.trading_date(now),
        mode=user.mode,
        status=view.status,
        snapshot=view.snapshot,
        plan=view.plan,
        locked_at=view.locked_at,
        lock_ends_at=view.lock_ends_at,
        market_open=app.calendar.is_market_open(now),
    )


@router.post("/mode", dependencies=Protected)
async def set_mode(body: ModeBody, request: Request, user: User) -> dict[str, str]:
    app = request.app.state.container
    if app.book.active(user.account_id, user.mode):
        raise ApiError(409, "OPEN_TRADE", "Close the open trade before switching mode")
    if body.mode is Mode.LIVE:
        if body.typed != LIVE_WORD:
            raise ApiError(422, "CONFIRM_WORD", f"Type {LIVE_WORD} to switch to live")
        if not app.brokers.has_live(user.account_id):
            raise ApiError(409, "BROKER_SESSION", "Connect the broker first")
        market = app.engine.feed.market
        if market is None or not market.is_real:
            raise ApiError(409, "SIMULATED_FEED", "Live orders need the real market feed")
    await app.auth.set_mode(user.session_id, body.mode)
    await app.audit.write(
        "mode_switched", app.clock.now(), user_id=user.id, data={"mode": body.mode}
    )
    return {"mode": body.mode}
