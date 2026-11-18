"""SkillService: Skill Test's accounts, runs and sessions, and the one way into Skill Test.

Skill Test replays past NIFTY and SENSEX days from Kotak's history, with options priced by a model
and fake rupees, in tables of its own. A run has its own "today": each session plays the next
trading day, and a played day never comes back, so no session can peek at the future. This
service never imports live trading's orders, guard, reconcile or trailing services (import-linter
holds it to that), and the only broker it uses is the PaperBroker each session is built with."""

import asyncio
import contextlib
import logging
import time as monotonic
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from coolcandle.db.tables import SkillRun, SkillSession, new_id
from coolcandle.domain.calendar import MARKET_OPEN, ist_datetime
from coolcandle.domain.models import Candle, Frozen
from coolcandle.domain.plan import PlanError
from coolcandle.domain.skill.charts import INTERVALS, REACH_DAYS, fold
from coolcandle.domain.skill.models import (
    INDICES,
    REPLAY_SPEEDS,
    PlanStatus,
    RunStatus,
    SessionPlan,
    SessionState,
    SkillSettings,
)
from coolcandle.domain.skill.options import index_instrument, next_expiry, realised_vol
from coolcandle.domain.skill.report import ReportCard, report
from coolcandle.domain.skill.rules import confirm, plan_fits_fund, stricter_edit
from coolcandle.domain.skill.run import check_start_fund
from coolcandle.ports.clock import Clock
from coolcandle.ports.skill import IndexData, ReplayFactory, SkillBrokerFactory
from coolcandle.services.bus import EventBus
from coolcandle.services.market.candles import CandleBuilder
from coolcandle.services.skill.arena import (
    SkillArena,
    SkillBlocked,
    SkillError,
    SkillTicket,
    Volatility,
)
from coolcandle.services.skill.store import FINAL_STATES, SkillStore
from coolcandle.services.skill.views import (
    AccountView,
    ChainView,
    PreviewView,
    RunView,
    SessionSummary,
    SessionView,
    live_card,
    run_view,
    summary,
)

log = logging.getLogger(__name__)

__all__ = ["NewSession", "SkillBlocked", "SkillError", "SkillService", "SkillTicket"]

VIX = "INDIAVIX"
DEFAULT_VOL = Decimal("0.15")  # when there is neither India VIX nor enough closes to measure
REALISED_DAYS = 20
YEAR_BACK = timedelta(days=365)  # a run starts with a year of chart behind it, when there is one
MIN_BACK = timedelta(days=30)
TIMER_S = 1.0
CURSOR_SAVE_S = 5.0  # a replay's place is saved this often, so a restart resumes there
RETIRE_WORD = "RETIRE"
NO_HISTORY = "No index history yet. Fetch it once with: uv run coolcandle fetch-index-history"


class NewSession(Frozen):
    index: str
    speed: int = 1
    plan: SessionPlan


class SkillService:
    def __init__(
        self,
        *,
        store: SkillStore,
        clock: Clock,
        bus: EventBus,
        history: IndexData,
        replays: ReplayFactory,
        brokers: SkillBrokerFactory,
        live_locked: Callable[[str], Awaitable[bool]],
        settings: SkillSettings,
    ) -> None:
        self.store = store
        self.clock = clock
        self.bus = bus
        self.history = history
        self.replays = replays
        self.brokers = brokers
        self.live_locked = live_locked  # by user id: is that user's live day locked?
        self.settings = settings
        self.arenas: dict[str, SkillArena] = {}
        self._plays: dict[str, asyncio.Task[None]] = {}
        self._timer: asyncio.Task[None] | None = None
        self._saved_at: dict[str, float] = {}

    # --- running ----------------------------------------------------------------------------

    async def start(self) -> None:
        """Bring back every started session, paused where it stopped."""
        for row in await self.store.running():
            try:
                run = await self.store.run(row.run_id)
                account = await self.store.account_of(run.account_id) if run else None
                if run is None or account is None:
                    continue
                await self._launch(row, run, account.user_id, revived=True)
            except Exception:
                log.exception("could not restore skill session %s", row.id)
        self._timer = asyncio.create_task(self._every_second(), name="skill-timer")

    async def stop(self) -> None:
        """Shut down in an order that never cuts a write in half: the timer first, then each
        replay paused and saved once its tick in progress has finished, then the replay loops."""
        if self._timer is not None:
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)
            self._timer = None
        for arena in list(self.arenas.values()):
            if arena.replay is None:
                continue
            async with arena.guard:
                arena.replay.pause()
                arena.row.replay_cursor = arena.replay.cursor
                with contextlib.suppress(Exception):
                    await self.store.save(arena.row)
        plays = list(self._plays.values())
        for task in plays:
            task.cancel()
        await asyncio.gather(*plays, return_exceptions=True)
        self._plays.clear()

    async def _every_second(self) -> None:
        while True:
            await asyncio.sleep(TIMER_S)
            for arena in list(self.arenas.values()):
                try:
                    await self._housekeeping(arena)
                except Exception:
                    log.exception("skill timer failed for %s", arena.row.id)

    async def _housekeeping(self, arena: SkillArena) -> None:
        moment = monotonic.monotonic()
        if moment - self._saved_at.get(arena.row.id, 0.0) >= CURSOR_SAVE_S:
            self._saved_at[arena.row.id] = moment
            arena.live_locked = await self.live_locked(arena.user_id)
            if arena.replay is not None and arena.row.replay_cursor != arena.replay.cursor:
                arena.row.replay_cursor = arena.replay.cursor
                await self.store.save(arena.row)
        if arena.final:
            self._retire(arena)

    def _retire(self, arena: SkillArena) -> None:
        self.arenas.pop(arena.row.id, None)
        task = self._plays.pop(arena.row.id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _play(self, arena: SkillArena) -> None:
        """One replay's own loop. Its ticks go only to the user who owns the session."""
        replay = arena.replay
        assert replay is not None
        builder = CandleBuilder()
        owner = {"user_id": arena.user_id, "session_id": arena.row.id}
        try:
            async for tick in replay.ticks():
                await arena.on_tick(tick)
                self.bus.publish("skill.tick", {**owner, **tick.model_dump(mode="json")})
                for candle in builder.update(tick):
                    if candle.interval == "1m":
                        payload = {**owner, **candle.model_dump(mode="json")}
                        self.bus.publish("skill.candle", payload)
                if arena.final:
                    break
            await arena.check()  # the day's minutes ran out: square-off
        finally:
            if arena.final:
                self._retire(arena)

    # --- the stored days --------------------------------------------------------------------

    def _days(self) -> list[date]:
        """Trading days with history for any index, oldest first."""
        return sorted({day for index in INDICES for day in self.history.days(index)})

    def _next_day(self, day: date) -> date:
        later = [d for d in self._days() if d > day]
        return later[0] if later else day + timedelta(days=1)

    def _span(self) -> tuple[date, date, date] | None:
        """(first day, last day, earliest start) of the stored history."""
        days = self._days()
        if not days:
            return None
        first, last = days[0], days[-1]
        back = YEAR_BACK if first + YEAR_BACK <= last else MIN_BACK
        starts = [d for d in days if d >= first + back]
        return first, last, starts[0] if starts else last

    def _is_trading(self, index: str) -> Callable[[date], bool]:
        days = set(self.history.days(index))
        last = max(days) if days else date.min
        return lambda day: day in days or (day > last and day.weekday() < 5)

    def _volatility(self, index: str, day: date) -> Volatility:
        opens = ist_datetime(day, MARKET_OPEN)
        vix = self.history.window(VIX, opens, opens + timedelta(days=1))
        closes = [
            c.close for c in self.history.bars(index, "1d", opens - timedelta(days=45), opens)
        ][-(REALISED_DAYS + 1) :]
        fallback = realised_vol(closes) or DEFAULT_VOL
        if vix:
            return Volatility(
                stamps=tuple(c.start for c in vix),
                values=tuple(c.close / 100 for c in vix),
                fallback=vix[0].open / 100,
                source="INDIA_VIX",
            )
        return Volatility(stamps=(), values=(), fallback=fallback, source="REALISED")

    # --- accounts and runs ------------------------------------------------------------------

    async def account(self, user_id: str) -> AccountView:
        account = await self.store.account(user_id)
        runs = await self.store.runs(account.id)
        sessions = await self.store.sessions([run.id for run in runs])
        done = [s for s in sessions if s.state in FINAL_STATES]
        running = next((s for s in sessions if s.state not in FINAL_STATES), None)
        scores = [s.score for s in done if s.score is not None]
        active = next((run for run in runs if run.status == RunStatus.ACTIVE), None)
        span = self._span()
        return AccountView(
            run=run_view(active, self.settings) if active else None,
            running=summary(running) if running else None,
            recent=tuple(summary(s) for s in done[:5]),
            best_score=max(scores, default=None),
            sessions_played=len(done),
            runs=len(runs),
            indices=tuple(index for index in INDICES if self.history.days(index)),
            first_day=span[0] if span else None,
            last_day=span[1] if span else None,
            earliest_start=span[2] if span else None,
            lot_sizes=self.settings.lot_sizes,
            settings=self.settings,
            live_locked=await self.live_locked(user_id),
        )

    async def runs(self, user_id: str) -> list[RunView]:
        account = await self.store.account(user_id)
        return [run_view(run, self.settings) for run in await self.store.runs(account.id)]

    async def start_run(
        self, user_id: str, start_fund: Decimal, start_day: date, typed: str | None
    ) -> RunView:
        """A new fund, and the day it starts on: its "today". The chart shows the year before."""
        check_start_fund(start_fund)
        span = self._span()
        if span is None:
            raise SkillError("NO_HISTORY", NO_HISTORY)
        first, last, earliest = span
        if not earliest <= start_day <= last:
            raise SkillError(
                "START_DAY", f"Pick a day from {earliest:%d %b %Y} to {last:%d %b %Y}", 422
            )
        today = next(d for d in self._days() if d >= start_day)
        account = await self.store.account(user_id)
        active = await self.store.active_run(account.id)
        if active is not None:
            if typed != RETIRE_WORD:
                raise SkillError(
                    "RETIRE_CONFIRM", f"Type {RETIRE_WORD} to end this run and start a new one"
                )
            for row in await self.store.sessions([active.id]):
                if row.state == SessionState.ACTIVE:
                    raise SkillError("SESSION_RUNNING", "End the running session first")
                if row.state == SessionState.DRAFT:
                    await self.store.delete_session(row)
            active.status = RunStatus.RETIRED
            active.ended_at = self.clock.now()
            await self.store.save(active)
        run = await self.store.add_run(account.id, start_fund, today, self.clock.now())
        return run_view(run, self.settings)

    async def sessions(self, user_id: str, run_id: str) -> list[SessionSummary]:
        account = await self.store.account(user_id)
        run = await self.store.run(run_id)
        if run is None or run.account_id != account.id:
            raise SkillError("RUN_NOT_FOUND", "No such run", 404)
        return [summary(row) for row in await self.store.sessions([run_id])]

    # --- sessions ---------------------------------------------------------------------------

    async def _owned(self, user_id: str, session_id: str) -> tuple[SkillSession, SkillRun]:
        arena = self.arenas.get(session_id)
        if arena is not None and arena.user_id == user_id:
            return arena.row, arena.run
        row = await self.store.session(session_id)
        run = await self.store.run(row.run_id) if row is not None else None
        account = await self.store.account(user_id)
        if row is None or run is None or run.account_id != account.id:
            raise SkillError("SESSION_NOT_FOUND", "No such session", 404)
        return row, run

    async def _arena(self, user_id: str, session_id: str) -> SkillArena:
        """The running arena, or one rebuilt from the database to answer about a session that
        is not running (a draft, or one that is over)."""
        row, run = await self._owned(user_id, session_id)
        arena = self.arenas.get(session_id)
        if arena is None:
            arena = self._make(row, run, user_id)
            arena.restore(
                await self.store.trades(row.id),
                await self.store.events(row.id),
                await self.store.orders(row.id),
            )
        arena.live_locked = await self.live_locked(user_id)
        return arena

    def _make(self, row: SkillSession, run: SkillRun, user_id: str) -> SkillArena:
        return SkillArena(
            row=row,
            run=run,
            user_id=user_id,
            expiry=next_expiry(row.index, row.day, self._is_trading(row.index)),
            volatility=self._volatility(row.index, row.day),
            next_day=self._next_day,
            settings=self.settings,
            clock=self.clock,
            brokers=self.brokers,
            store=self.store,
            bus=self.bus,
        )

    async def _closed_by_live(self, user_id: str) -> None:
        if not self.settings.when_live_locked and await self.live_locked(user_id):
            raise SkillError(
                "LIVE_LOCKED", "Live is locked for today, so Skill Test is closed until it unlocks"
            )

    async def _running_in(self, run: SkillRun) -> SkillSession | None:
        rows = await self.store.sessions([run.id])
        return next((r for r in rows if r.state == SessionState.ACTIVE), None)

    async def create_session(self, user_id: str, new: NewSession) -> SessionView:
        """A draft for the run's next day: it starts once its plan is confirmed twice."""
        account = await self.store.account(user_id)
        run = await self.store.active_run(account.id)
        if run is None:
            raise SkillError("NO_RUN", "Start a run first")
        await self._closed_by_live(user_id)
        if await self._running_in(run) is not None:
            raise SkillError("SESSION_RUNNING", "Finish the running session first")
        if new.index not in INDICES:
            raise SkillError("UNKNOWN_INDEX", f"Pick one of {', '.join(INDICES)}", 422)
        if new.speed not in REPLAY_SPEEDS:
            raise SkillError("REPLAY_SPEED", f"Speed must be one of {REPLAY_SPEEDS}", 422)
        if run.today not in self.history.days(new.index):
            raise SkillError(
                "NO_DAY",
                f"There is no {new.index} history for {run.today:%d %b %Y}. "
                "This run has played every stored day.",
            )
        problem = plan_fits_fund(new.plan, run.cash)
        if problem is not None:
            raise SkillError("PLAN_FUND", problem)
        for row in await self.store.sessions([run.id]):
            if row.state == SessionState.DRAFT:
                await self.store.delete_session(row)  # a new draft replaces an unstarted one
        row = SkillSession(
            id=new_id(),
            run_id=run.id,
            index=new.index,
            day=run.today,
            speed=new.speed,
            plan=new.plan.model_dump(mode="json"),
            plan_status=PlanStatus.DRAFT,
            state=SessionState.DRAFT,
        )
        await self.store.save(row)
        return (await self._arena(user_id, row.id)).view()

    async def confirm(
        self, user_id: str, session_id: str, step: int, typed: str | None
    ) -> SessionView:
        row, run = await self._owned(user_id, session_id)
        if row.state != SessionState.DRAFT:
            raise SkillError("ALREADY_STARTED", "This session has already started")
        status = confirm(PlanStatus(row.plan_status), step, typed)
        if status is PlanStatus.STARTED:
            if run.status != RunStatus.ACTIVE:
                raise SkillError("NO_RUN", "This run has ended. Start a new one.")
            await self._closed_by_live(user_id)
            if await self._running_in(run) is not None:
                raise SkillError("SESSION_RUNNING", "Finish the running session first")
            problem = plan_fits_fund(SessionPlan.model_validate(row.plan), run.cash)
            if problem is not None:
                raise SkillError("PLAN_FUND", problem)
            await self._launch(row, run, user_id, revived=False)
        else:
            row.plan_status = status
            await self.store.save(row)
        return (await self._arena(user_id, session_id)).view()

    async def _launch(self, row: SkillSession, run: SkillRun, user_id: str, revived: bool) -> None:
        """Start a confirmed session, or bring a started one back after a restart."""
        arena = self._make(row, run, user_id)
        if revived:
            arena.restore(
                await self.store.trades(row.id),
                await self.store.events(row.id),
                await self.store.orders(row.id),
            )
        opens = ist_datetime(row.day, MARKET_OPEN)
        candles = self.history.window(row.index, opens, opens + timedelta(days=1))
        if not candles:
            raise SkillError("NO_DATA", f"There is no {row.index} history for that day", 422)
        start = row.replay_cursor or candles[0].start
        arena.replay = self.replays(
            index_instrument(row.index),
            candles,
            start,
            speed=row.speed,
            paused=revived,
            stop_side=arena.stop_side,
        )
        if not revived:
            row.plan_status = PlanStatus.STARTED
            row.state = SessionState.ACTIVE
            row.started_at = arena.now()
            row.start_equity = run.cash
            await self.store.save(row)
        arena.live_locked = await self.live_locked(user_id)
        self.arenas[row.id] = arena
        self._plays[row.id] = asyncio.create_task(self._play(arena), name=f"replay-{row.id}")
        arena.publish(force=True)

    async def edit_plan(self, user_id: str, session_id: str, plan: SessionPlan) -> SessionView:
        row, run = await self._owned(user_id, session_id)
        if row.state == SessionState.DRAFT:
            problem = plan_fits_fund(plan, run.cash)
            if problem is not None:
                raise SkillError("PLAN_FUND", problem)
            row.plan = plan.model_dump(mode="json")
            row.plan_status = PlanStatus.DRAFT  # a changed plan is confirmed again
            await self.store.save(row)
        elif row.state == SessionState.ACTIVE:
            arena = await self._arena(user_id, session_id)
            stricter_edit(arena.plan, plan)  # raises PlanError if anything loosens
            async with arena.guard:
                arena.plan = plan
                row.plan = plan.model_dump(mode="json")
                await self.store.save(row)
            arena.publish(force=True)
        else:
            raise PlanError("SESSION_OVER", "This session is over")
        return (await self._arena(user_id, session_id)).view()

    async def view(self, user_id: str, session_id: str) -> SessionView:
        return (await self._arena(user_id, session_id)).view()

    async def chain(self, user_id: str, session_id: str) -> ChainView:
        arena = await self._arena(user_id, session_id)
        if session_id not in self.arenas:
            raise SkillError("NOT_PLAYING", "The option chain shows while the session plays")
        return arena.chain()

    async def replay_control(
        self,
        user_id: str,
        session_id: str,
        action: Literal["play", "pause", "speed"],
        speed: int | None,
    ) -> SessionView:
        arena = await self._arena(user_id, session_id)
        replay = arena.replay
        if replay is None or arena.final or session_id not in self.arenas:
            raise SkillError("NOT_PLAYING", "This session is not playing")
        if action == "play":
            replay.play()
        elif action == "pause":
            replay.pause()
        else:
            if speed not in REPLAY_SPEEDS:
                raise SkillError("REPLAY_SPEED", f"Speed must be one of {REPLAY_SPEEDS}", 422)
            replay.set_speed(speed)
            arena.row.speed = speed
        arena.row.replay_cursor = replay.cursor
        await self.store.save(arena.row)
        info = arena.replay_info()
        if info is not None:
            self.bus.publish(
                "skill.replay",
                {"user_id": user_id, "session_id": session_id, **info.model_dump(mode="json")},
            )
        arena.publish(force=True)
        return arena.view()

    async def preview(self, user_id: str, session_id: str, ticket: SkillTicket) -> PreviewView:
        return (await self._arena(user_id, session_id)).preview(ticket)

    async def enter(self, user_id: str, session_id: str, ticket: SkillTicket) -> SessionView:
        arena = await self._arena(user_id, session_id)
        await arena.enter(ticket)
        return arena.view()

    async def _trade_arena(self, user_id: str, trade_id: str) -> SkillArena:
        session_id = await self.store.trade_session(trade_id)
        if session_id is None:
            raise SkillError("TRADE_NOT_FOUND", "No such trade", 404)
        return await self._arena(user_id, session_id)

    async def move_stop(self, user_id: str, trade_id: str, stop: Decimal) -> SessionView:
        arena = await self._trade_arena(user_id, trade_id)
        await arena.move_stop(trade_id, stop)
        return arena.view()

    async def exit(self, user_id: str, trade_id: str) -> SessionView:
        arena = await self._trade_arena(user_id, trade_id)
        await arena.exit(trade_id)
        return arena.view()

    async def end(self, user_id: str, session_id: str) -> SessionView:
        """Ending early is quitting, not a lock, and costs no points. The day is still spent."""
        arena = await self._arena(user_id, session_id)
        if arena.row.state == SessionState.DRAFT:
            raise SkillError("NOT_STARTED", "This session has not started")
        await arena.end()
        self._retire(arena)
        return arena.view()

    async def report(self, user_id: str, session_id: str) -> ReportCard:
        row, run = await self._owned(user_id, session_id)
        if row.state not in FINAL_STATES or row.started_at is None:
            raise SkillError("NOT_OVER", "The report card is ready when the session ends")
        trades = await self.store.trades(row.id)
        plan = SessionPlan.model_validate(row.plan)
        card = live_card(row, plan, trades, await self.store.events(row.id), self.settings)
        return report(
            trades,
            card,
            started_at=row.started_at,
            fund_before=row.start_equity if row.start_equity is not None else run.start_fund,
            start_fund=run.start_fund,
            settings=self.settings,
        )

    async def candles(self, user_id: str, session_id: str, interval: str) -> list[Candle]:
        """The session's chart in any timeframe: the stored days before it, then today's
        minutes up to the replay's cursor, never one past it, while it plays or after."""
        if interval not in INTERVALS:
            raise SkillError("INTERVAL", f"Pick one of {', '.join(INTERVALS)}", 422)
        row, _ = await self._owned(user_id, session_id)
        opens = ist_datetime(row.day, MARKET_OPEN)
        before = self.history.bars(
            row.index, interval, opens - timedelta(days=REACH_DAYS[interval]), opens
        )
        arena = self.arenas.get(session_id)
        if arena is not None and arena.replay is not None:
            today = arena.replay.played()
        elif row.replay_cursor is not None:
            until = row.replay_cursor.replace(second=0, microsecond=0)
            today = self.history.window(row.index, opens, until)
        else:
            today = []
        return before + fold(today, interval)
