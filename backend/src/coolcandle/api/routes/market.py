from datetime import date
from typing import Literal

from fastapi import APIRouter, Request

from coolcandle.api.deps import Protected, User
from coolcandle.api.errors import ApiError
from coolcandle.api.schemas import EventBody
from coolcandle.domain.calendar import to_ist
from coolcandle.domain.events import MarketEvent, MarketHoliday
from coolcandle.domain.models import Candle, Instrument
from coolcandle.services.events.service import TaggedHeadline
from coolcandle.services.market.chain import OptionChain, build_chain

router = APIRouter(prefix="/market")


@router.get("/candles")
async def candles(
    request: Request, _: User, key: str, interval: Literal["1m", "5m"] = "1m"
) -> list[Candle]:
    book = request.app.state.container.candles
    earlier: list[Candle] = book.history(key, interval)
    today: list[Candle] = book.with_forming(key, interval)
    first_today = today[0].start if today else None
    kept = [c for c in earlier if first_today is None or c.start < first_today]
    return kept + today


@router.get("/option-chain")
async def option_chain(request: Request, _: User, underlying: str = "NIFTY") -> OptionChain:
    app = request.app.state.container
    today = to_ist(app.clock.now()).date()
    return build_chain(underlying, today, app.instruments, app.quotes)


@router.get("/instruments")
async def instruments(request: Request, _: User, underlying: str = "NIFTY") -> list[Instrument]:
    found: list[Instrument] = request.app.state.container.instruments.options(underlying)
    return found


@router.get("/holidays")
async def holidays(request: Request, _: User, start: date, end: date) -> list[MarketHoliday]:
    if end < start or (end - start).days > 366:
        raise ApiError(422, "BAD_RANGE", "Ask for up to a year of holidays at a time")
    found: list[MarketHoliday] = await request.app.state.container.events.holidays(start, end)
    return found


@router.get("/events")
async def events(request: Request, _: User) -> list[MarketEvent]:
    app = request.app.state.container
    upcoming: list[MarketEvent] = await app.events.upcoming(
        app.calendar.trading_date(app.clock.now())
    )
    return upcoming


@router.post("/events", dependencies=Protected)
async def add_event(body: EventBody, request: Request, _: User) -> MarketEvent:
    event = MarketEvent(**body.model_dump())
    await request.app.state.container.events.add(event)
    return event


@router.delete("/events", dependencies=Protected)
async def remove_event(request: Request, _: User, day: str, name: str) -> dict[str, bool]:
    app = request.app.state.container
    removed = await app.events.remove(
        date.fromisoformat(day), name, app.calendar.trading_date(app.clock.now())
    )
    if not removed:
        raise ApiError(409, "EVENT_FROZEN", "Today's calendar cannot be changed")
    return {"ok": True}


@router.get("/news")
async def news(request: Request, _: User) -> list[TaggedHeadline]:
    headlines: list[TaggedHeadline] = request.app.state.container.events.headlines
    return headlines
