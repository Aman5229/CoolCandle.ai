"""One running Skill Test session: a replayed NIFTY or SENSEX day, options priced by the model, its
own PaperBroker, and the monitor that locks it.

This is Skill Test's one door to a broker, as OrderService is live trading's. The broker it talks
to is built for this session alone, from `SkillBrokerFactory`, and fills against this session's
model prices only. An entry is checked first, its stop goes in the moment it fills, and an exit
turns that stop into a market order, never cancel-then-market. Every change is written through to
the skill_* tables, so a restart picks the session up where it was."""

import asyncio
import contextlib
import time
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from coolcandle.db.tables import SkillOrder, SkillRun, SkillSession, new_id
from coolcandle.domain.calendar import MARKET_OPEN, ist_datetime
from coolcandle.domain.costs import round_trip
from coolcandle.domain.models import (
    Action,
    Candle,
    Decision,
    Frozen,
    Intent,
    OptionType,
    Order,
    OrderChanges,
    OrderRequest,
    OrderStatus,
    OrderType,
    Reason,
    Side,
    Tick,
)
from coolcandle.domain.skill.models import (
    EventKind,
    ExitKind,
    RunStatus,
    SessionPlan,
    SessionState,
    SkillEvent,
    SkillLock,
    SkillSettings,
    SkillTrade,
    StopSide,
)
from coolcandle.domain.skill.options import (
    SEGMENT,
    TICK,
    model_price,
    option_instrument,
    parse_key,
    premium,
    quote,
    strikes,
    years_to_expiry,
)
from coolcandle.domain.skill.rules import (
    EntryCheck,
    cash_needed,
    check_entry,
    lock_trigger,
    session_block,
    stop_range,
    trade_pnl,
    worst_case_loss,
)
from coolcandle.domain.skill.run import is_blown
from coolcandle.domain.skill.score import ScoreCard
from coolcandle.ports.broker import BrokerError
from coolcandle.ports.clock import Clock
from coolcandle.ports.skill import Replay, SkillBrokerFactory
from coolcandle.services.bus import EventBus
from coolcandle.services.skill.store import FINAL_STATES, SkillStore
from coolcandle.services.skill.views import (
    ChainRow,
    ChainView,
    OptionQuote,
    PreviewView,
    ReplayInfo,
    SessionView,
    live_card,
    session_view,
    snapshot,
    state_now,
)

PUBLISH_GAP_S = 0.25  # the HUD updates at most four times a second, and at once on a change
ENTRY, STOP, TARGET = "ENTRY", "STOP", "TARGET"


class SkillError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SkillBlocked(Exception):
    def __init__(self, decision: Decision) -> None:
        super().__init__("Skill Test refused the order")
        self.decision = decision


class SkillTicket(Frozen):
    """Buy an option: CE or PE, on the session's index and expiry."""

    instrument_key: str
    lots: int
    order_type: Literal[OrderType.MARKET, OrderType.LIMIT] = OrderType.MARKET
    price: Decimal | None = None  # LIMIT only
    stop: Decimal | None = None  # optional here so a missing stop is refused, and counted
    target: Decimal | None = None


@dataclass
class Legs:
    """The broker orders behind one trade."""

    entry: str | None = None
    stop: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class Volatility:
    """The model's volatility through the day: India VIX minute by minute, or one realised
    figure when Kotak had no VIX for the day."""

    stamps: tuple[datetime, ...]
    values: tuple[Decimal, ...]  # fractions: 0.14 is 14%
    fallback: Decimal
    source: str

    def at(self, moment: datetime) -> Decimal:
        place = bisect_right(self.stamps, moment)
        return self.values[place - 1] if place else self.fallback


class SkillArena:
    def __init__(
        self,
        *,
        row: SkillSession,
        run: SkillRun,
        user_id: str,
        expiry: date,
        volatility: Volatility,
        next_day: Callable[[date], date],
        settings: SkillSettings,
        clock: Clock,
        brokers: SkillBrokerFactory,
        store: SkillStore,
        bus: EventBus,
    ) -> None:
        self.row = row
        self.run = run
        self.user_id = user_id
        self.expiry = expiry
        self.volatility = volatility
        self.next_day = next_day
        self.settings = settings
        self.base_clock = clock
        self.store = store
        self.bus = bus
        self.plan = SessionPlan.model_validate(row.plan)
        self.lot_size = settings.lot_sizes[row.index]
        self.step = settings.strike_steps[row.index]
        self.rate = settings.risk_free_pct / 100
        self.replay: Replay | None = None
        self.spot: Tick | None = None  # the replayed index
        self.broker = brokers(self, self.quote, TICK)  # a tick of slippage on market orders
        self.trades: dict[str, SkillTrade] = {}
        self.events: list[SkillEvent] = []
        self.legs: dict[str, Legs] = {}
        self.live_locked = False
        self.guard = asyncio.Lock()  # ticks and commands take turns
        self._exiting: dict[str, ExitKind] = {}
        self._blown = False
        self._published = 0.0

    # --- the session's clock and prices -----------------------------------------------------

    def now(self) -> datetime:
        """The replay's time. The broker stamps its orders with this."""
        if self.replay is not None:
            return self.replay.clock.now()
        return self.row.replay_cursor or ist_datetime(self.row.day, MARKET_OPEN)

    @property
    def final(self) -> bool:
        return self.row.state in FINAL_STATES

    def prices_fresh(self) -> bool:
        replay = self.replay
        if self.spot is None or replay is None:
            return False
        return not replay.paused and not replay.finished

    def _model(self, key: str, spot: Decimal, moment: datetime) -> Decimal:
        _, expiry, strike, kind = parse_key(key)
        years = years_to_expiry(moment, expiry)
        return model_price(kind, spot, strike, years, self.volatility.at(moment), self.rate)

    def quote(self, key: str) -> Tick | None:
        """An option's model quote at the index's last price. The broker fills against it."""
        if self.spot is None or not key.startswith(f"skill|{self.row.index}|"):
            return None
        moment = self.now()
        price = self._model(key, self.spot.ltp, moment)
        bid, ask = quote(price, self.settings.spread_pct)
        return Tick(
            instrument_key=key, ts=moment, received_at=moment, ltp=premium(price), bid=bid, ask=ask
        )

    def option_quote(self, key: str) -> OptionQuote | None:
        tick = self.quote(key)
        if tick is None or tick.bid is None or tick.ask is None:
            return None
        _, expiry, strike, kind = parse_key(key)
        contract = option_instrument(self.row.index, expiry, strike, kind, self.lot_size)
        return OptionQuote(
            key=key,
            trading_symbol=contract.trading_symbol,
            ltp=tick.ltp,
            bid=tick.bid,
            ask=tick.ask,
        )

    def _key(self, strike: Decimal, kind: OptionType) -> str:
        return option_instrument(self.row.index, self.expiry, strike, kind, self.lot_size).key

    def chain(self) -> ChainView:
        if self.spot is None:
            raise SkillError("NO_PRICE", "The replay has not started yet")
        rows = []
        for strike in strikes(self.spot.ltp, self.step, self.settings.chain_width):
            ce = self.option_quote(self._key(strike, OptionType.CE))
            pe = self.option_quote(self._key(strike, OptionType.PE))
            if ce is not None and pe is not None:
                rows.append(ChainRow(strike=strike, ce=ce, pe=pe))
        return ChainView(
            index=self.row.index,
            spot=self.spot.ltp,
            expiry=self.expiry,
            atm=strikes(self.spot.ltp, self.step, 0)[0],
            lot_size=self.lot_size,
            vol=self.volatility.at(self.now()),
            vol_source=self.volatility.source,
            rows=tuple(rows),
        )

    def stop_side(self, candle: Candle) -> StopSide | None:
        """For the replay: when the open trade's stop and target both fall inside this minute,
        name the index extreme that reaches the stop, so the replay walks there first."""
        trade = self.open_trade()
        if trade is None or trade.entry is None or trade.target is None:
            return None
        at_low = self._model(trade.instrument_key, candle.low, candle.start)
        at_high = self._model(trade.instrument_key, candle.high, candle.start)
        call = parse_key(trade.instrument_key)[3] is OptionType.CE
        lowest, highest = (at_low, at_high) if call else (at_high, at_low)
        if lowest <= trade.stop and highest >= trade.target:
            return "low" if call else "high"
        return None

    # --- restore ------------------------------------------------------------------------------

    def restore(
        self, trades: list[SkillTrade], events: list[SkillEvent], orders: list[SkillOrder]
    ) -> None:
        """Rebuild after a restart. The broker gets its open stop and target back, so a trade is
        still protected before the first new price arrives."""
        self.trades = {t.id: t for t in trades}
        self.events = list(events)
        self.broker.restore(
            [
                Order(
                    id=o.broker_order_id,
                    broker_order_id=o.broker_order_id,
                    request=OrderRequest.model_validate(o.request),
                    status=OrderStatus(o.status),
                    filled_qty=o.filled_qty,
                    avg_price=o.avg_price,
                    updated_at=o.ts,
                )
                for o in orders
            ]
        )
        for o in orders:
            if o.trade_id is None:
                continue
            legs = self.legs.setdefault(o.trade_id, Legs())
            if o.role == ENTRY:
                legs.entry = o.broker_order_id
            elif o.role == STOP:
                legs.stop = o.broker_order_id
            elif o.role == TARGET:
                legs.target = o.broker_order_id

    # --- what the screens see -----------------------------------------------------------------

    def open_trade(self) -> SkillTrade | None:
        return next((t for t in self.trades.values() if t.closed_at is None), None)

    def _marks(self) -> dict[str, Decimal]:
        """Each open option's bid: what it could be sold for now."""
        marks: dict[str, Decimal] = {}
        for trade in self.trades.values():
            tick = self.quote(trade.instrument_key) if trade.closed_at is None else None
            if tick is not None and tick.bid is not None:
                marks[trade.instrument_key] = tick.bid
        return marks

    def score(self) -> ScoreCard:
        return live_card(
            self.row, self.plan, list(self.trades.values()), self.events, self.settings
        )

    def replay_info(self) -> ReplayInfo | None:
        if self.replay is not None:
            return ReplayInfo(
                cursor=self.replay.cursor,
                speed=self.replay.speed,
                paused=self.replay.paused,
                finished=self.replay.finished,
            )
        if self.row.replay_cursor is not None:
            return ReplayInfo(
                cursor=self.row.replay_cursor,
                speed=self.row.speed,
                paused=True,
                finished=self.final,
            )
        return None

    def view(self) -> SessionView:
        trade = self.open_trade()
        return session_view(
            row=self.row,
            run=self.run,
            expiry=self.expiry,
            lot_size=self.lot_size,
            trades=list(self.trades.values()),
            events=self.events,
            marks=self._marks(),
            open_quote=self.option_quote(trade.instrument_key) if trade else None,
            spot=self.spot.ltp if self.spot else None,
            now=self.now(),
            prices_fresh=self.prices_fresh(),
            replay=self.replay_info(),
            live_locked=self.live_locked,
            settings=self.settings,
        )

    def publish(self, *, force: bool = False) -> None:
        moment = time.monotonic()
        if not force and moment - self._published < PUBLISH_GAP_S:
            return
        self._published = moment
        self.bus.publish(
            "skill.session", {"user_id": self.user_id, **self.view().model_dump(mode="json")}
        )

    def _emit(self, kind: str, **data: object) -> None:
        self.bus.publish(kind, {"user_id": self.user_id, "session_id": self.row.id, **data})

    # --- the tick path ------------------------------------------------------------------------

    async def on_tick(self, tick: Tick) -> None:
        """A new index price: every option in play is repriced and handed to the broker."""
        async with self.guard:
            if self.final:
                return
            self.spot = tick
            for trade in list(self.trades.values()):
                if trade.closed_at is None:
                    option = self.quote(trade.instrument_key)
                    if option is not None:
                        self.broker.on_tick(option)
            await self._drain()
            await self._check()
        self.publish()

    async def check(self) -> None:
        async with self.guard:
            await self._check()

    async def _check(self) -> None:
        if self.final:
            return
        if self._blown:
            await self._finish(SkillLock.BLOWN)
            return
        snap = snapshot(list(self.trades.values()), self._marks(), self.now(), self.settings)
        lock = lock_trigger(self.plan, snap)
        if lock is None and self.replay is not None and self.replay.finished:
            lock = SkillLock.SQUARE_OFF  # the day's minutes ran out
        if lock is not None:
            await self._finish(lock)

    # --- orders and fills -----------------------------------------------------------------

    async def _drain(self) -> None:
        """Hand every order update to the trade it belongs to. Handling one can place or cancel
        another (the stop after an entry fills), so keep going until the broker is quiet."""
        while updates := self.broker.drain_updates():
            for order in updates:
                await self.store.save_order(self.row.id, self._role(order), order)
                await self._on_order(order)

    def _role(self, order: Order) -> str:
        legs = self.legs.get(order.request.trade_id or "")
        if legs is not None and order.broker_order_id == legs.target:
            return TARGET
        if legs is not None and order.broker_order_id == legs.stop:
            return STOP
        return ENTRY

    async def _on_order(self, order: Order) -> None:
        trade = self.trades.get(order.request.trade_id or "")
        legs = self.legs.get(order.request.trade_id or "")
        if trade is None or legs is None or trade.closed_at is not None:
            return
        broker_id = order.broker_order_id
        if broker_id == legs.entry:
            if order.status is OrderStatus.COMPLETE and order.avg_price is not None:
                await self._entry_filled(trade, legs, order.avg_price)
            elif order.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                await self._save(trade.model_copy(update={"closed_at": self.now()}))
        elif broker_id == legs.stop:
            if order.status is OrderStatus.COMPLETE and order.avg_price is not None:
                kind = self._exiting.pop(trade.id, ExitKind.STOP)
                await self._closed(trade, order.avg_price, kind, legs.target)
            elif order.status is OrderStatus.REJECTED:
                # The price was already through the stop: no position stays without one, so
                # sell it at market now.
                legs.stop = await self._place(trade, Intent.EXIT, OrderType.MARKET)
        elif broker_id == legs.target and order.status is OrderStatus.COMPLETE:
            if order.avg_price is not None:
                await self._closed(trade, order.avg_price, ExitKind.TARGET, legs.stop)

    async def _place(
        self,
        trade: SkillTrade,
        intent: Intent,
        order_type: OrderType,
        *,
        price: Decimal | None = None,
        trigger: Decimal | None = None,
    ) -> str:
        order = await self.broker.place_order(
            OrderRequest(
                instrument_key=trade.instrument_key,
                trading_symbol=trade.trading_symbol,
                exchange_segment=SEGMENT,
                side=Side.BUY if intent is Intent.ENTRY else Side.SELL,
                qty=trade.qty,
                order_type=order_type,
                price=price,
                trigger=trigger,
                intent=intent,
                trade_id=trade.id,
            )
        )
        return order.broker_order_id

    async def _entry_filled(self, trade: SkillTrade, legs: Legs, price: Decimal) -> None:
        risk = worst_case_loss(price, trade.stop, trade.qty, TICK, self.settings.rates)
        trade = trade.model_copy(
            update={
                "entry": price,
                "opened_at": self.now(),
                "stop_at_fill": trade.stop,
                "r_risk": risk,
            }
        )
        await self._save(trade)
        await self._event(
            EventKind.ENTRY,
            trade.id,
            option=trade.trading_symbol,
            qty=trade.qty,
            entry=str(price),
            stop=str(trade.stop),
            r_risk=str(risk),
        )
        # The stop goes in the moment the entry fills; the target rests beside it.
        legs.stop = await self._place(trade, Intent.STOP, OrderType.SL_M, trigger=trade.stop)
        if trade.target is not None and self.trades[trade.id].closed_at is None:
            legs.target = await self._place(trade, Intent.EXIT, OrderType.LIMIT, price=trade.target)

    async def _closed(
        self, trade: SkillTrade, price: Decimal, kind: ExitKind, other: str | None
    ) -> None:
        if trade.entry is None:
            return
        pnl, charges = trade_pnl(trade.entry, price, trade.qty, self.settings.rates)
        trade = trade.model_copy(
            update={
                "exit": price,
                "closed_at": self.now(),
                "exit_kind": kind,
                "pnl": pnl,
                "fees": charges,
            }
        )
        await self._save(trade)
        await self._event(EventKind.EXIT, trade.id, exit_kind=kind, exit=str(price), pnl=str(pnl))
        if other is not None:
            with contextlib.suppress(BrokerError):  # already filled or cancelled
                await self.broker.cancel_order(other)
        self.run.cash += pnl
        self.run.peak_equity = max(self.run.peak_equity, self.run.cash)
        if is_blown(self.run.cash, self.run.start_fund, self.settings):
            self.run.status = RunStatus.BLOWN
            self.run.ended_at = self.now()
            self._blown = True
        await self.store.save(self.run)

    async def _save(self, trade: SkillTrade) -> None:
        self.trades[trade.id] = trade
        await self.store.save_trade(self.row.id, trade)
        self._emit("skill.trade", trade=trade.model_dump(mode="json"))

    async def _event(self, kind: EventKind, trade_id: str | None = None, **data: object) -> None:
        event = SkillEvent(id=new_id(), ts=self.now(), kind=kind, trade_id=trade_id, data=data)
        self.events.append(event)
        await self.store.add_event(self.row.id, event)

    # --- lock and end ---------------------------------------------------------------------

    async def _finish(self, lock: SkillLock | None) -> None:
        """Lock (a limit, or the square-off) or end (the user stopped early). An open trade is
        sold at market first, as the live day lock does. The run's day moves on either way: a
        played day never comes back."""
        trade = self.open_trade()
        if trade is not None:
            legs = self.legs.get(trade.id, Legs())
            with contextlib.suppress(BrokerError):
                if trade.entry is None and legs.entry is not None:
                    await self.broker.cancel_order(legs.entry)
                elif legs.stop is not None:
                    self._exiting[trade.id] = ExitKind.LOCK
                    await self.broker.modify_order(
                        legs.stop, OrderChanges(order_type=OrderType.MARKET)
                    )
            await self._drain()
        if self._blown:
            lock = SkillLock.BLOWN
        if lock is not None:
            await self._event(EventKind.LOCKED, reason=lock)
        self.row.state = SessionState.LOCKED if lock is not None else SessionState.ENDED
        self.row.lock_reason = lock
        self.row.ended_at = self.now()
        self.row.end_equity = self.run.cash
        if self.replay is not None:
            self.replay.pause()
            self.row.replay_cursor = self.replay.cursor
        self.run.today = max(self.run.today, self.next_day(self.row.day))
        await self.store.save(self.run)
        card = await self._store_score()
        if lock is not None:
            self._emit("skill.locked", reason=lock, score=card.score)
        if self._blown:
            self._emit("skill.run_blown", run_id=self.run.id)
        self.publish(force=True)

    async def _store_score(self) -> ScoreCard:
        card = self.score()
        self.row.score = card.score
        self.row.score_breakdown = [line.model_dump(mode="json") for line in card.lines]
        await self.store.save(self.row)
        return card

    # --- commands -------------------------------------------------------------------------

    def _contract(self, ticket: SkillTicket) -> str | Reason:
        """The option's name, or why it is not one this session trades."""
        refused = Reason(code="UNKNOWN_OPTION", message="That is not an option in this session")
        try:
            index, expiry, strike, kind = parse_key(ticket.instrument_key)
        except ValueError:
            return refused
        if index != self.row.index or expiry != self.expiry or strike % self.step != 0:
            return refused
        return option_instrument(index, expiry, strike, kind, self.lot_size).trading_symbol

    def _entry_price(self, ticket: SkillTicket) -> Decimal | None:
        """Where the entry would fill: a market buy at the model's ask, one tick worse."""
        if ticket.order_type is OrderType.LIMIT:
            return ticket.price
        tick = self.quote(ticket.instrument_key)
        if tick is None or tick.ask is None:
            return None
        return tick.ask + TICK

    def decide(self, ticket: SkillTicket) -> Decision:
        extra: list[Reason] = []
        if self.live_locked and not self.settings.when_live_locked:
            extra.append(
                Reason(
                    code="LIVE_LOCKED",
                    message="Live is locked for today, so Skill Test is closed until it unlocks",
                )
            )
        contract = self._contract(ticket)
        if isinstance(contract, Reason):
            extra.append(contract)
        snap = snapshot(list(self.trades.values()), self._marks(), self.now(), self.settings)
        state = state_now(self.row, self.plan, snap)
        run_active = self.run.status == RunStatus.ACTIVE
        entry = None if isinstance(contract, Reason) else self._entry_price(ticket)
        if entry is None:
            blocked = session_block(state, run_active)
            extra.extend([blocked] if blocked is not None else [])
            if not isinstance(contract, Reason):
                extra.append(Reason(code="NO_PRICE", message="There is no price to buy at yet"))
            return Decision(action=Action.BLOCK, reasons=tuple(extra))
        check = EntryCheck(
            lots=ticket.lots,
            qty=ticket.lots * self.lot_size,
            entry=entry,
            stop=ticket.stop,
            target=ticket.target,
            tick=TICK,
            state=state,
            run_active=run_active,
            prices_fresh=self.prices_fresh(),
            has_open_trade=snap.open_trade,
            trades_taken=snap.trades_taken,
            net_pnl=snap.net_pnl,
            free_cash=self.run.cash,
        )
        decision = check_entry(self.plan, check, self.settings)
        reasons = (*extra, *decision.reasons)
        return Decision(action=Action.BLOCK if reasons else Action.ALLOW, reasons=reasons)

    def preview(self, ticket: SkillTicket) -> PreviewView:
        known = not isinstance(self._contract(ticket), Reason)
        entry = self._entry_price(ticket) if known else None
        worst = charges = need = reward_risk = None
        allowed: tuple[Decimal, Decimal] | None = None
        qty = ticket.lots * self.lot_size
        rates = self.settings.rates
        if entry is not None and qty > 0:
            need = cash_needed(entry, qty, rates)
            snap = snapshot(list(self.trades.values()), self._marks(), self.now(), self.settings)
            loss_left = self.plan.loss_limit + min(snap.net_pnl, Decimal("0"))
            budget = min(self.plan.max_loss_per_trade, loss_left)
            allowed = stop_range(entry, qty, TICK, budget, self.settings)
            if ticket.stop is not None and ticket.stop < entry:
                worst = worst_case_loss(entry, ticket.stop, qty, TICK, rates)
                charges = round_trip(entry, ticket.stop, qty, rates)
                if ticket.target is not None and ticket.target > entry:
                    reward = ticket.target - entry
                    reward_risk = (reward / (entry - ticket.stop)).quantize(Decimal("0.01"))
        return PreviewView(
            entry=entry,
            lowest_stop=allowed[0] if allowed else None,
            highest_stop=allowed[1] if allowed else None,
            worst_case_loss=worst,
            charges=charges,
            cash_needed=need,
            reward_risk=reward_risk,
            decision=self.decide(ticket),
        )

    async def enter(self, ticket: SkillTicket) -> SkillTrade:
        async with self.guard:
            decision = self.decide(ticket)
            contract = self._contract(ticket)
            if not decision.allowed or isinstance(contract, Reason) or ticket.stop is None:
                # Live quietly prevents; Skill Test also counts the attempt, for the score.
                await self._event(
                    EventKind.BLOCKED, codes=[reason.code for reason in decision.reasons]
                )
                if self.final:
                    await self._store_score()  # the attempt still costs points after a lock
                self.publish(force=True)
                raise SkillBlocked(decision)
            trade = SkillTrade(
                id=new_id(),
                instrument_key=ticket.instrument_key,
                trading_symbol=contract,
                qty=ticket.lots * self.lot_size,
                stop_at_fill=ticket.stop,
                stop=ticket.stop,
                target=ticket.target,
                r_risk=Decimal("0"),
                opened_at=self.now(),
            )
            await self._save(trade)
            self.legs[trade.id] = Legs()
            self.legs[trade.id].entry = await self._place(
                trade, Intent.ENTRY, ticket.order_type, price=ticket.price
            )
            await self._drain()
            await self._check()
            result = self.trades[trade.id]
        self.publish(force=True)
        return result

    def _trade(self, trade_id: str) -> tuple[SkillTrade, Legs]:
        trade = self.trades.get(trade_id)
        if trade is None or trade.closed_at is not None or self.final:
            raise SkillError("TRADE_CLOSED", "That trade is no longer open")
        return trade, self.legs.setdefault(trade_id, Legs())

    async def move_stop(self, trade_id: str, stop: Decimal) -> SkillTrade:
        async with self.guard:
            trade, legs = self._trade(trade_id)
            tick = self.quote(trade.instrument_key)
            if tick is not None and tick.bid is not None and stop >= tick.bid:
                raise SkillError("STOP_SIDE", "The stop must stay below the option's price")
            if trade.entry is None:
                await self._save(trade.model_copy(update={"stop": stop, "stop_at_fill": stop}))
            else:
                further = stop < trade.stop_at_fill
                if legs.stop is not None:
                    await self.broker.modify_order(legs.stop, OrderChanges(trigger=stop))
                moved = trade.model_copy(
                    update={"stop": stop, "stop_widened": trade.stop_widened or further}
                )
                await self._save(moved)
                await self._event(
                    EventKind.STOP_MOVED,
                    trade.id,
                    stop_from=str(trade.stop),
                    stop_to=str(stop),
                    widened=further,
                )
            await self._drain()
            await self._check()
            result = self.trades[trade_id]
        self.publish(force=True)
        return result

    async def exit(self, trade_id: str) -> SkillTrade:
        """Sell at market by turning the stop into a market order. A pending entry is cancelled
        instead."""
        async with self.guard:
            trade, legs = self._trade(trade_id)
            if trade.entry is None:
                if legs.entry is not None:
                    await self.broker.cancel_order(legs.entry)
            elif legs.stop is not None:
                self._exiting[trade.id] = ExitKind.MANUAL
                await self.broker.modify_order(legs.stop, OrderChanges(order_type=OrderType.MARKET))
            await self._drain()
            await self._check()
            result = self.trades[trade_id]
        self.publish(force=True)
        return result

    async def end(self) -> None:
        async with self.guard:
            if not self.final:
                await self._finish(None)
