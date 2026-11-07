from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import ModifyStopBody, TicketBody
from coolcandle.domain.exits import ExitPlan, StopRule, plan_exit
from coolcandle.domain.models import EntryTicket, ExitReason, OrderSource, Trade
from coolcandle.services.journal.service import DaySummary, TimelineEvent
from coolcandle.services.orders.service import Preview

router = APIRouter()


def _ticket(body: TicketBody) -> EntryTicket:
    return EntryTicket(**body.model_dump(), source=OrderSource.MANUAL)


@router.post("/orders/preview", dependencies=Protected)
async def preview(body: TicketBody, request: Request, user: User) -> Preview:
    result: Preview = await request.app.state.container.orders.preview(
        user.account_id, user.mode, _ticket(body)
    )
    return result


@router.get("/orders/exit-plan")
async def exit_plan(
    request: Request,
    _: User,
    key: str,
    entry: Decimal,
    rule: StopRule,
    value: Decimal,
    target_r: Decimal = Decimal("2"),
) -> ExitPlan:
    """Stop and target from a rule, set before the entry."""
    app = request.app.state.container
    instrument = app.instruments.get(key)
    if instrument is None:
        raise ApiError(404, "UNKNOWN_INSTRUMENT", "That instrument is not in today's list")
    return plan_exit(
        entry,
        rule,
        value,
        target_r,
        instrument.tick_size,
        candles=app.candles.closed(key, "1m"),
    )


@router.post("/trades", dependencies=Protected)
async def submit(body: TicketBody, request: Request, user: User) -> Trade:
    trade: Trade = await request.app.state.container.orders.submit_entry(
        user.account_id, user.mode, _ticket(body)
    )
    return trade


def _active(request: Request, user: User, trade_id: str) -> None:
    trade = request.app.state.container.book.get(trade_id)
    if trade is None or trade.account_id != user.account_id:
        raise ApiError(404, "TRADE_NOT_FOUND", "That trade is not active")


@router.post("/trades/{trade_id}/exit", dependencies=Protected)
async def exit_trade(trade_id: str, request: Request, user: User) -> Trade:
    _active(request, user, trade_id)
    trade: Trade = await request.app.state.container.orders.exit_trade(trade_id, ExitReason.MANUAL)
    return trade


@router.post("/trades/{trade_id}/cancel", dependencies=Protected)
async def cancel(trade_id: str, request: Request, user: User) -> Trade:
    _active(request, user, trade_id)
    trade: Trade = await request.app.state.container.orders.cancel_entry(trade_id)
    return trade


@router.post("/trades/{trade_id}/stop", dependencies=Protected)
async def move_stop(trade_id: str, body: ModifyStopBody, request: Request, user: User) -> Trade:
    _active(request, user, trade_id)
    trade: Trade = await request.app.state.container.orders.modify_stop(trade_id, body.trigger)
    return trade


@router.get("/trades")
async def list_trades(request: Request, user: User, day: date | None = None) -> list[Trade]:
    app = request.app.state.container
    when = day or app.calendar.trading_date(app.clock.now())
    trades: list[Trade] = await app.journal.trades(user.account_id, user.mode, when)
    return trades


@router.get("/trades/{trade_id}")
async def timeline(trade_id: str, request: Request, user: User) -> list[TimelineEvent]:
    app = request.app.state.container
    if await app.journal.trade(user.account_id, trade_id) is None:
        raise ApiError(404, "TRADE_NOT_FOUND", "No such trade")
    events: list[TimelineEvent] = await app.journal.timeline(trade_id)
    return events


@router.get("/journal")
async def journal(request: Request, user: User, start: date, end: date) -> list[DaySummary]:
    days: list[DaySummary] = await request.app.state.container.journal.days(
        user.account_id, user.mode, start, end
    )
    return days
