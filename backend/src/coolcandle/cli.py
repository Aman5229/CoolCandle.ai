"""Command line: create-admin, replay-day, backtest, flush-day. Nothing here places live orders."""

import argparse
import asyncio
import getpass
from datetime import date
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

    args = parser.parse_args()
    if args.command == "create-admin":
        asyncio.run(_create_admin(args.email))
    elif args.command == "flush-day":
        _flush_day(args.day)
    else:
        asyncio.run(_backtest(args.setup, args.start, args.end, args.daily_loss_limit))
