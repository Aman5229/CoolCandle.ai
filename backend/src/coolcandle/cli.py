"""Command line: create-admin, backtest, flush-day, fetch-index-history. Nothing here places live
orders."""

import argparse
import asyncio
import getpass
from datetime import date, datetime, timedelta
from decimal import Decimal

from coolcandle.config import Settings
from coolcandle.main import build, seed_calendar


async def _create_admin(email: str) -> None:
    settings = Settings()  # type: ignore[call-arg]
    container = build(settings)
    await seed_calendar(container)
    password = getpass.getpass("Password: ")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters")
    secret = await container.auth.create_admin(email, password)
    print("Add this to your authenticator app. It is shown only once:")
    print(f"otpauth://totp/CoolCandle:{email}?secret={secret}&issuer=CoolCandle")


def _flush_day(day: date) -> None:
    from coolcandle.services.recorder.recorder import Recorder

    settings = Settings()  # type: ignore[call-arg]
    path = Recorder(settings.data_dir).flush_to_parquet(day)
    print(path or "Nothing recorded for that day")


async def _backtest(setup: str, start: date, end: date, loss_limit: Decimal) -> None:
    from coolcandle.backtesting import run_backtest

    settings = Settings()  # type: ignore[call-arg]
    print(await run_backtest(settings, setup, start, end, loss_limit))


# Kotak's names for the history Skill Test replays. India VIX drives the option model; without
# it the model falls back to each day's realised volatility.
HISTORY_SYMBOLS = {
    "NIFTY": ("nse_cm|Nifty 50",),
    "SENSEX": ("bse_cm|SENSEX",),
    "INDIAVIX": ("nse_cm|India VIX", "nse_cm|INDIA VIX"),
}


async def _fetch_index_history(years: int) -> None:
    """Kotak's 1-minute history for Skill Test. It needs only the consumer key: no TOTP and no
    login, so it never ends a trading session. Running it again tops the files up."""
    from neo_api_client.neo_api import NeoAPI
    from sqlalchemy import select

    from coolcandle.adapters.brokers.kotak_neo.history import HistoryUnavailable, history
    from coolcandle.adapters.market.history.store import IndexHistory
    from coolcandle.db.session import make_engine, make_sessions
    from coolcandle.db.tables import BrokerAccount
    from coolcandle.domain.calendar import IST
    from coolcandle.domain.skill.options import index_key
    from coolcandle.services.accounts.vault import Vault

    settings = Settings()  # type: ignore[call-arg]
    engine = make_engine(settings.database_url)
    async with make_sessions(engine)() as session:
        account = await session.scalar(
            select(BrokerAccount).where(BrokerAccount.credentials_enc.is_not(None)).limit(1)
        )
    await engine.dispose()
    if account is None or account.credentials_enc is None:
        raise SystemExit("Save your Kotak credentials on the Brokers page first")
    vault = Vault(settings.secret_key.get_secret_value())
    consumer_key = vault.decrypt_dict(account.credentials_enc).get("consumer_key")
    if not consumer_key:
        raise SystemExit("The saved Kotak credentials have no consumer key")
    client = NeoAPI(consumer_key=consumer_key, environment="prod")

    store = IndexHistory(settings.data_dir)
    end = datetime.now(IST).date()
    start = end - timedelta(days=365 * years)
    for name, keys in HISTORY_SYMBOLS.items():
        print(f"{name}: asking Kotak for {start} to {end} (slow on purpose: Kotak rate-limits)")
        total = 0
        stored = store.days(name)
        # Carry on from the last stored day: a run that stopped part way is not repeated.
        begin = max(start, stored[-1]) if stored else start
        for key in keys:
            # A year at a time, each saved as it arrives, so a failure late on keeps the rest.
            first = begin
            while first <= end:
                last = min(first + timedelta(days=364), end)
                try:
                    candles = await history(
                        client, key, "1m", first, last, candle_key=index_key(name)
                    )
                except HistoryUnavailable as exc:
                    raise SystemExit(
                        f"{exc}. Everything before it is saved: run the command again to go on."
                    ) from None
                if candles:
                    store.save(name, candles)
                    total += len(candles)
                print(f"  {first} to {last}: {len(candles):,} minutes", flush=True)
                first = last + timedelta(days=1)
            if total or stored:
                break
        if not total and not stored:
            note = " The option model will use realised volatility." if name == "INDIAVIX" else ""
            print(f"{name}: Kotak returned no minutes.{note}")
            continue
        days = store.days(name)
        print(
            f"{name}: {total:,} minutes saved. On disk: {days[0]} to {days[-1]}, "
            f"{len(days)} trading days."
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="coolcandle")
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="Create the one admin user")
    admin.add_argument("email")

    flush = commands.add_parser("flush-day", help="Convert a recorded day to Parquet")
    flush.add_argument("day", type=date.fromisoformat)

    test = commands.add_parser("backtest", help="Replay recorded days through the real guard")
    test.add_argument("setup", choices=["opening_range", "level_breakout"])
    test.add_argument("start", type=date.fromisoformat)
    test.add_argument("end", type=date.fromisoformat)
    test.add_argument("--daily-loss-limit", type=Decimal, default=Decimal("5000"))

    fetch = commands.add_parser(
        "fetch-index-history", help="Download NIFTY, SENSEX and India VIX minutes for Skill Test"
    )
    fetch.add_argument("--years", type=int, default=3)

    args = parser.parse_args()
    if args.command == "create-admin":
        asyncio.run(_create_admin(args.email))
    elif args.command == "flush-day":
        _flush_day(args.day)
    elif args.command == "fetch-index-history":
        asyncio.run(_fetch_index_history(args.years))
    else:
        asyncio.run(_backtest(args.setup, args.start, args.end, args.daily_loss_limit))
