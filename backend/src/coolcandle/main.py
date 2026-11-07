"""The app factory, and the one place that wires ports to adapters."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from coolcandle import seeds
from coolcandle.adapters.agent.http import HttpAgent
from coolcandle.adapters.brokers.catalog import CATALOG
from coolcandle.adapters.brokers.kotak_neo.broker import KotakBroker
from coolcandle.adapters.brokers.kotak_neo.feed import KotakMarketData
from coolcandle.adapters.brokers.paper.broker import PaperBroker
from coolcandle.adapters.clock import SimClock, SystemClock
from coolcandle.adapters.market.idle import IdleMarket
from coolcandle.adapters.market.sim import SimulatedMarket
from coolcandle.adapters.news.rss import RssSource
from coolcandle.adapters.notify.telegram import TelegramNotifier
from coolcandle.api.errors import add_handlers
from coolcandle.api.routes import agent as agent_routes
from coolcandle.api.routes import auth, brokers, health, market, plan, signals, trades
from coolcandle.api.routes import settings as settings_routes
from coolcandle.api.ws import router as ws_router
from coolcandle.config import Settings
from coolcandle.db.session import Sessions, make_engine, make_sessions
from coolcandle.db.tables import Holiday, MarketEventRow
from coolcandle.domain.calendar import MarketCalendar, ist_datetime
from coolcandle.domain.costs import CostRates
from coolcandle.domain.guard import GuardConfig
from coolcandle.domain.models import Mode
from coolcandle.domain.setups.level_breakout import LevelBreakout
from coolcandle.domain.setups.opening_range import OpeningRangeBreakout
from coolcandle.domain.signals import SignalConfig
from coolcandle.logs import setup_logging
from coolcandle.ports.agent import Agent
from coolcandle.ports.broker import Broker, BrokerSpec
from coolcandle.ports.clock import Clock
from coolcandle.ports.market_data import MarketData
from coolcandle.ports.news_source import NewsSource
from coolcandle.ports.notifier import Notifier
from coolcandle.ports.signal_source import SignalSource
from coolcandle.services.accounts.service import AccountService
from coolcandle.services.accounts.vault import Vault
from coolcandle.services.agent.service import AgentService
from coolcandle.services.agent.tools import AgentTools
from coolcandle.services.audit import Audit
from coolcandle.services.auth.service import AuthService
from coolcandle.services.bus import EventBus
from coolcandle.services.engine import Engine
from coolcandle.services.events.service import EventService
from coolcandle.services.guard.monitor import DayMonitor
from coolcandle.services.guard.service import GuardService
from coolcandle.services.journal.service import Journal
from coolcandle.services.market.candles import CandleBuilder
from coolcandle.services.market.feed import FeedManager
from coolcandle.services.market.instruments import InstrumentBook
from coolcandle.services.market.quotes import QuoteBook
from coolcandle.services.notify.service import NotifyService
from coolcandle.services.orders.book import TradeBook
from coolcandle.services.orders.routing import BrokerRouter
from coolcandle.services.orders.service import OrderService
from coolcandle.services.orders.tokens import ConfirmTokens
from coolcandle.services.plans.service import PlanService
from coolcandle.services.reconcile.service import Reconciler
from coolcandle.services.recorder.recorder import Recorder
from coolcandle.services.scheduler.service import Job, Scheduler
from coolcandle.services.settings import SettingsStore
from coolcandle.services.signals.service import SignalService
from coolcandle.services.trailing.service import TrailingService

log = logging.getLogger(__name__)

SIM_OPEN = time(9, 20)


def cost_rates(settings: Settings) -> CostRates:
    return CostRates(
        brokerage_per_order=settings.brokerage_per_order,
        stt_sell_pct=settings.stt_sell_pct,
        exchange_pct=settings.exchange_pct,
        sebi_pct=settings.sebi_pct,
        stamp_buy_pct=settings.stamp_buy_pct,
        gst_pct=settings.gst_pct,
    )


def sim_start(calendar: MarketCalendar, now: datetime, chosen: date | None) -> datetime:
    """A simulated session opens at 09:20 on the chosen day, or the next trading day."""
    return ist_datetime(chosen or calendar.next_trading_day(now.date()), SIM_OPEN)


def default_market(settings: Settings, clock: Clock) -> MarketData:
    """The simulator locally. In production, nothing until the broker's real feed connects."""
    if settings.market_source == "kotak":
        return IdleMarket()
    return SimulatedMarket(clock, settings.sim_seed, settings.sim_speed)


@dataclass
class Container:
    settings: Settings
    clock: Clock
    calendar: MarketCalendar
    db: Sessions
    bus: EventBus
    audit: Audit
    auth: AuthService
    accounts: AccountService
    plans: PlanService
    guard: GuardService
    orders: OrderService
    book: TradeBook
    brokers: BrokerRouter
    instruments: InstrumentBook
    quotes: QuoteBook
    candles: CandleBuilder
    events: EventService
    journal: Journal
    signals: SignalService
    agent: AgentService
    notify: NotifyService
    settings_store: SettingsStore
    engine: Engine
    market: MarketData
    catalog: tuple[BrokerSpec, ...]


def build(
    settings: Settings, clock: Clock | None = None, market: MarketData | None = None
) -> Container:
    """Wire everything. Nothing here touches the network or the database."""
    calendar = MarketCalendar(day for day, _ in seeds.HOLIDAYS)
    real = SystemClock()
    if clock is None:
        simulated = settings.market_source == "sim"
        start = sim_start(calendar, real.now(), settings.sim_date)
        clock = SimClock(start, settings.sim_speed) if simulated else real

    engine_db = make_engine(settings.database_url)
    db = make_sessions(engine_db)
    bus = EventBus()
    audit = Audit(db)
    vault = Vault(settings.secret_key.get_secret_value())
    rates = cost_rates(settings)
    config = GuardConfig(live_order_cap=settings.live_order_cap)

    quotes = QuoteBook()
    candles = CandleBuilder()
    instruments = InstrumentBook()
    book = TradeBook(db, clock, bus)
    feed = FeedManager(instruments)

    notifiers: list[Notifier] = []
    if settings.telegram_bot_token is not None and settings.telegram_chat_id is not None:
        notifiers.append(
            TelegramNotifier(
                settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id
            )
        )
    notify = NotifyService(
        bus, notifiers, settings.secret_key.get_secret_value(), settings.public_url
    )

    the_clock = clock
    brokers = BrokerRouter(lambda _account: PaperBroker(the_clock, quotes.get, settings.paper_cash))
    sources: list[NewsSource] = [RssSource(url) for url in settings.news_rss_urls]
    events = EventService(db, sources)
    plans = PlanService(db, clock, calendar, audit)
    guard = GuardService(
        db=db,
        clock=clock,
        calendar=calendar,
        plans=plans,
        book=book,
        quotes=quotes,
        instruments=instruments,
        brokers=brokers,
        events=events,
        rates=rates,
        config=config,
    )
    tokens = ConfirmTokens()
    orders = OrderService(
        clock=clock,
        calendar=calendar,
        book=book,
        guard=guard,
        brokers=brokers,
        quotes=quotes,
        instruments=instruments,
        plans=plans,
        tokens=tokens,
        alerts=notify,
        audit=audit,
        rates=rates,
        config=config,
    )
    monitor = DayMonitor(
        clock=clock,
        guard=guard,
        orders=orders,
        plans=plans,
        bus=bus,
        audit=audit,
        alerts=notify,
    )
    reconciler = Reconciler(book, brokers, orders)
    trailing = TrailingService(clock, book, orders, candles, settings.trailing_live_enabled)
    setups: list[SignalSource] = (
        [OpeningRangeBreakout(), LevelBreakout()] if settings.signals_enabled else []
    )
    signal_service = SignalService(
        db=db,
        clock=clock,
        calendar=calendar,
        setups=setups,
        candles=candles,
        instruments=instruments,
        quotes=quotes,
        orders=orders,
        plans=plans,
        guard=guard,
        events=events,
        tokens=tokens,
        notify=notify,
        bus=bus,
        config=SignalConfig(),
    )
    remote: Agent | None = None
    if settings.agent_url is not None and settings.agent_token is not None:
        remote = HttpAgent(settings.agent_url, settings.agent_token.get_secret_value())
    tools = AgentTools(
        clock=clock,
        calendar=calendar,
        guard=guard,
        signals=signal_service,
        orders=orders,
        book=book,
        events=events,
    )
    agent = AgentService(agent=remote, tools=tools, clock=clock, calendar=calendar)

    engine_holder: list[Engine] = []

    async def on_connected(account_id: str, broker: Broker) -> None:
        """After the daily connect: load the live day, switch to Kotak's feed, and reconcile."""
        engine = engine_holder[0]
        await engine.load_account(account_id, Mode.LIVE)
        if settings.market_source == "kotak" and isinstance(broker, KotakBroker):
            kotak_market = KotakMarketData(broker, clock_ref, settings.data_dir)
            await engine.use_market(kotak_market)
            engine.track("order-feed", kotak_market.order_feed_signals(engine.order_wake))
        await engine.reconcile_all()

    clock_ref = clock
    accounts = AccountService(
        db=db,
        clock=clock,
        vault=vault,
        audit=audit,
        brokers=brokers,
        book=book,
        live_factory=lambda _broker_id: KotakBroker(clock_ref),
        on_connected=on_connected,
    )
    # Simulated prices must never reach the recordings: they are V2's training data.
    recorder = Recorder(settings.data_dir) if settings.market_source == "kotak" else None
    engine = Engine(
        clock=clock,
        calendar=calendar,
        bus=bus,
        quotes=quotes,
        candles=candles,
        instruments=instruments,
        feed=feed,
        recorder=recorder,
        brokers=brokers,
        book=book,
        plans=plans,
        guard=guard,
        orders=orders,
        monitor=monitor,
        reconciler=reconciler,
        trailing=trailing,
        signals=signal_service,
        events=events,
        accounts=accounts,
    )
    engine_holder.append(engine)

    async def flush_recorder() -> None:
        if recorder is not None:
            recorder.flush_to_parquet(calendar.trading_date(clock_ref.now()))

    async def refresh_scrips() -> None:
        current = engine.feed.market
        if current is not None and current.is_real:
            await engine.feed.use(current)

    engine.scheduler = Scheduler(
        clock,
        calendar,
        [
            Job(time(8, 30), "scrip master", refresh_scrips),
            Job(time(9, 0), "day reset", engine.new_day),
            Job(time(15, 45), "recorder flush", flush_recorder),
        ],
    )

    return Container(
        settings=settings,
        clock=clock,
        calendar=calendar,
        db=db,
        bus=bus,
        audit=audit,
        auth=AuthService(db, real, vault, audit),
        accounts=accounts,
        plans=plans,
        guard=guard,
        orders=orders,
        book=book,
        brokers=brokers,
        instruments=instruments,
        quotes=quotes,
        candles=candles,
        events=events,
        journal=Journal(db),
        signals=signal_service,
        agent=agent,
        notify=notify,
        settings_store=SettingsStore(db),
        engine=engine,
        market=market or default_market(settings, clock),
        catalog=CATALOG,
    )


async def seed_calendar(container: Container) -> None:
    """Make the holidays match the exchange's list exactly, add missing events, then load the
    holiday list. A holiday dropped from the list is deleted: left behind, it would keep the
    market closed on a day it is open."""
    async with container.db() as session, session.begin():
        official = dict(seeds.HOLIDAYS)
        stored = (await session.execute(select(Holiday).where(Holiday.exchange == "NSE"))).scalars()
        known = set()
        for row in stored:
            if row.date not in official:
                await session.delete(row)
                continue
            row.name = official[row.date]
            known.add(row.date)
        for day, name in seeds.HOLIDAYS:
            if day not in known:
                session.add(Holiday(date=day, exchange="NSE", name=name))
        have = {
            (row.day, row.name) for row in (await session.execute(select(MarketEventRow))).scalars()
        }
        for event in seeds.EVENTS:
            if (event.day, event.name) not in have:
                session.add(
                    MarketEventRow(
                        day=event.day,
                        name=event.name,
                        at=event.at,
                        impact=event.impact,
                        block_before_minutes=event.block_before_minutes,
                        block_after_minutes=event.block_after_minutes,
                    )
                )
    async with container.db() as session:
        rows = await session.execute(select(Holiday.date))
        container.calendar.holidays = frozenset(rows.scalars())


def settings_or_default(settings: Settings | None) -> Settings:
    return settings if settings is not None else Settings()  # type: ignore[call-arg]


def create_app(settings: Settings | None = None) -> FastAPI:
    setup_logging()
    config = settings_or_default(settings)
    container = build(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await seed_calendar(container)
        await container.engine.start(container.market)
        yield
        await container.engine.stop()

    app = FastAPI(title="CoolCandle.ai", lifespan=lifespan)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.allowed_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    add_handlers(app)
    for router in (
        health.router,
        auth.router,
        brokers.router,
        plan.router,
        market.router,
        trades.router,
        signals.router,
        settings_routes.router,
        agent_routes.router,
    ):
        app.include_router(router, prefix="/api")
    app.include_router(agent_routes.internal)
    app.include_router(ws_router)
    return app
