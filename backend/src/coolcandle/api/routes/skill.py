"""Skill Test: past NIFTY and SENSEX days replayed, options priced by a model. Nothing here
reaches live trading."""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from coolcandle.api.deps import Protected, User
from coolcandle.api.schemas import ConfirmBody
from coolcandle.domain.models import Candle
from coolcandle.domain.skill.models import SessionPlan
from coolcandle.domain.skill.report import ReportCard
from coolcandle.services.skill.service import NewSession, SkillService, SkillTicket
from coolcandle.services.skill.views import (
    AccountView,
    ChainView,
    PreviewView,
    RunView,
    SessionSummary,
    SessionView,
)

router = APIRouter(prefix="/skill")


class RunBody(BaseModel):
    start_fund: Decimal
    start_day: date  # the run's "today": the chart shows the year before it
    typed: str | None = None  # RETIRE, to end the current run first


class ReplayBody(BaseModel):
    action: Literal["play", "pause", "speed"]
    speed: int | None = None


class StopBody(BaseModel):
    stop: Decimal = Field(gt=0)


def _skill(request: Request) -> SkillService:
    service: SkillService = request.app.state.container.skill
    return service


@router.get("/account")
async def account(request: Request, user: User) -> AccountView:
    return await _skill(request).account(user.id)


@router.get("/runs")
async def runs(request: Request, user: User) -> list[RunView]:
    return await _skill(request).runs(user.id)


@router.post("/runs", dependencies=Protected)
async def start_run(body: RunBody, request: Request, user: User) -> RunView:
    return await _skill(request).start_run(user.id, body.start_fund, body.start_day, body.typed)


@router.get("/runs/{run_id}/sessions")
async def run_sessions(run_id: str, request: Request, user: User) -> list[SessionSummary]:
    return await _skill(request).sessions(user.id, run_id)


@router.post("/sessions", dependencies=Protected)
async def create_session(body: NewSession, request: Request, user: User) -> SessionView:
    return await _skill(request).create_session(user.id, body)


@router.get("/sessions/{session_id}")
async def session(session_id: str, request: Request, user: User) -> SessionView:
    return await _skill(request).view(user.id, session_id)


@router.post("/sessions/{session_id}/confirm", dependencies=Protected)
async def confirm(session_id: str, body: ConfirmBody, request: Request, user: User) -> SessionView:
    return await _skill(request).confirm(user.id, session_id, body.step, body.typed)


@router.patch("/sessions/{session_id}/plan", dependencies=Protected)
async def edit_plan(
    session_id: str, body: SessionPlan, request: Request, user: User
) -> SessionView:
    return await _skill(request).edit_plan(user.id, session_id, body)


@router.post("/sessions/{session_id}/replay", dependencies=Protected)
async def replay(session_id: str, body: ReplayBody, request: Request, user: User) -> SessionView:
    return await _skill(request).replay_control(user.id, session_id, body.action, body.speed)


@router.post("/sessions/{session_id}/preview", dependencies=Protected)
async def preview(session_id: str, body: SkillTicket, request: Request, user: User) -> PreviewView:
    return await _skill(request).preview(user.id, session_id, body)


@router.post("/sessions/{session_id}/orders", dependencies=Protected)
async def enter(session_id: str, body: SkillTicket, request: Request, user: User) -> SessionView:
    return await _skill(request).enter(user.id, session_id, body)


@router.patch("/trades/{trade_id}/stop", dependencies=Protected)
async def move_stop(trade_id: str, body: StopBody, request: Request, user: User) -> SessionView:
    return await _skill(request).move_stop(user.id, trade_id, body.stop)


@router.post("/trades/{trade_id}/exit", dependencies=Protected)
async def exit_trade(trade_id: str, request: Request, user: User) -> SessionView:
    return await _skill(request).exit(user.id, trade_id)


@router.post("/sessions/{session_id}/end", dependencies=Protected)
async def end(session_id: str, request: Request, user: User) -> SessionView:
    return await _skill(request).end(user.id, session_id)


@router.get("/sessions/{session_id}/report")
async def report(session_id: str, request: Request, user: User) -> ReportCard:
    return await _skill(request).report(user.id, session_id)


@router.get("/sessions/{session_id}/candles")
async def candles(
    session_id: str, request: Request, user: User, interval: str = "1m"
) -> list[Candle]:
    """The session's chart in a timeframe: 1m, 5m, 15m, 1h or 1d. Never past the cursor."""
    return await _skill(request).candles(user.id, session_id, interval)


@router.get("/sessions/{session_id}/chain")
async def chain(session_id: str, request: Request, user: User) -> ChainView:
    """Strikes around the money, with model prices, while the session plays."""
    return await _skill(request).chain(user.id, session_id)
