
# CoolCandle.ai Blueprint

> Version 1.0 · 30 Oct 2026 · Owner: Aman (GitHub `Aman5229`)
>
> This is the single source of truth for what CoolCandle.ai is, how it is built, and in what
> order. It is written so a coding agent (Claude Code) can read it once and build V1 without
> guessing. Anything marked **DEFAULT** is a recommendation Aman has not confirmed yet; build
> it that way, keep it easy to change, and record the final answer in §2.

---

## 0. Read this first (rules for anyone writing code here)

1. **Protection first.** CoolCandle exists to stop overtrading. Any feature that weakens the
   daily lock is a bug, even if it was asked for in passing.
2. **One door to the broker.** Every order, from any source (manual ticket, V2 signal, V3 agent,
   V4 trailing), goes through `OrderService.submit()`, which calls the risk guard first. No
   other code may call `Broker.place_order()` / `modify_order()` / `cancel_order()`.
3. **Code owns the numbers.** Strike, entry, stop, target and quantity are computed by tested,
   deterministic code. An LLM may explain or talk, never invent a price or a quantity.
4. **No position without a stop.** A stop-loss order goes to the exchange the moment an entry
   fills, for the filled quantity. Exits modify that stop; never "cancel stop, then market".
5. **Paper by default.** Every new login starts in paper mode. Live mode is a deliberate switch.
6. **Money is `Decimal`.** Never `float` for prices, P&L or charges. Times are stored as
   timezone-aware UTC and shown in IST (`Asia/Kolkata`).
7. **The domain layer is pure.** `domain/` has no I/O, no clock, no network. It is where the
   guard rules live and it gets the most tests.
8. **Never log secrets.** MPIN, TOTP, tokens, consumer keys and passwords never appear in logs,
   errors, exceptions or API responses.
9. **Never place a live order in tests or scripts** without an explicit `--live` flag and a
   human at the keyboard.
10. **Small PRs, tests with every change, CI green before merge.**

---

## 1. Product

### 1.1 The problem
Most retail intraday options traders lose money, and a large share of that comes from
overtrading: revenge trades after a loss, giving back a good morning, taking "one more" trade.

### 1.2 What CoolCandle does
CoolCandle is a trading platform on top of the user's own demat account (Kotak Neo first).
Each morning the trader sets a plan: a profit target, a loss limit and a maximum number of
trades. Once any one is reached, CoolCandle exits open trades, cancels pending orders and
**locks ordering on the platform until the next trading day**.

Later versions add a decision model that proposes trades (V2), a voice trading buddy that
talks the trader through them and places them after confirmation (V3), and an automatic
trailing stop (V4).

### 1.3 Who it is for
V1: Aman only, on his own Kotak Neo account. The data model is multi-user from day one so
more users and brokers can be added without a rewrite, but sign-up is closed in V1.

### 1.4 What it is not
- Not a broker. Users bring their own demat account; CoolCandle connects through the broker's API.
- Not able to stop trading in the broker's own app. It counts that P&L, but cannot block it.
- Not investment advice, and it sends no signals to anyone but the account owner.

---

## 2. Decisions

| # | Question | Decision | Status |
|---|---|---|---|
| D1 | How does the user sign in? | CoolCandle account: email + password + authenticator TOTP (CoolCandle's own). Then **connect the broker each morning** with the broker's TOTP + MPIN. | DEFAULT |
| D2 | "Only ask twice" for the day plan | Plan is a draft until confirmed **twice** (a summary dialog, then typing `LOCK`). After that it is frozen until the next trading day. Changes that only make it **stricter** are still allowed (lower target, lower loss limit, fewer trades, earlier cutoff), each with one confirmation. | DEFAULT |
| D3 | Day profit target hit with a trade open | **Exit immediately** and lock (V1). In V4 this becomes "stop new entries, trail the open trade with profit protected". | DEFAULT |
| D4 | Trades placed in the broker's own app | **Count both** their P&L and their number toward today's limits. | DEFAULT |
| D5 | What can be traded | **NIFTY and SENSEX index options, buying only** (long CE / long PE), intraday product. Config list, so BANKNIFTY etc. can be added. | DEFAULT |
| D6 | V3 voice confirmation | Voice proposes; a **tap on screen** confirms. | DEFAULT |
| D7 | V2 success measure | Replace "80–90% accuracy" with a **profit-after-costs gate on unseen data** (§16.2). | DEFAULT |
| D8 | Build order after V1 | **V1 → V4 (trailing) → V2 → V3.** Trailing is pure rules and needs no AI; voice depends on V2. Version names stay as they are. | DEFAULT |
| D9 | Repo | **New repo** for CoolCandle.ai; old `CoolHead.ai` archived, used for ideas only. | **CONFIRMED** |
| D10 | Hosting | Small VM in India (Mumbai/Bangalore) with a **static IP** and a persistent disk. | DEFAULT |
| D11 | Simultaneous trades | **One open trade at a time** in V1. | DEFAULT |

When Aman confirms or changes a row, update the table and the sections that depend on it.

---

## 3. Roadmap

| Version | Goal | Done when |
|---|---|---|
| **V1** Overtrading guard | Sign-in, broker connect (Kotak Neo), live charts and option chain, day plan, orders with mandatory stop, day lock, journal, recorder. | Aman uses it as his daily driver on live money for two weeks with no lock leaks and no state mismatches. |
| **V4** Trailing stop (built second, D8) | Move the stop behind price after a trigger; at the trade target, trail instead of exiting. | Tested on replayed days and two weeks of paper, then enabled live. |
| **V2** Decision model | Encode Aman's setups (support/resistance, breakouts, patterns) as detectors; backtest; propose trades with entry, stop, target. | At least one setup passes the gate in §16.2 on unseen data and a month of paper signals. |
| **V3** Alerts + voice buddy | Telegram/push alerts linking to a confirm screen; a chat then voice agent that explains V2's trades and places them after a tap. | A full voice session on paper: alert → explain → tap → entry → exit; all agent evals pass. |

Nothing from a later version is built until the earlier one is "done", except the **extension
points** V1 must leave in place (§4.3).

---

## 4. Architecture

### 4.1 Shape
A **modular monolith**: one Python backend process, one React web app, one Postgres database,
deployed as Docker containers on one server. Modules talk through small interfaces (ports), so
later versions add modules rather than change existing ones.

```
 Browser (React)
   │  HTTPS (REST)  +  WebSocket (/ws: ticks, day status, orders, alerts)
   ▼
 ┌───────────────────────────── backend (FastAPI) ─────────────────────────────┐
 │ api/        routes, ws hub, auth dependency                                │
 │   │                                                                        │
 │ services/   auth · accounts · plans · guard · orders · market · recorder   │
 │             reconcile · journal · scheduler   [V2 signals] [V3 agent,      │
 │             notify] [V4 trailing]                                          │
 │   │  uses                                                                  │
 │ domain/     pure rules & models: Plan, DaySnapshot, Decision, Trade state  │
 │             machine, sizing, costs, market calendar                        │
 │   │  through                                                               │
 │ ports/      Broker · MarketData · Clock · [SignalSource] [Notifier]         │
 │             [TrailingPolicy]                                               │
 │   ▲ implemented by                                                         │
 │ adapters/   brokers/kotak_neo · brokers/paper · market/replay              │
 └──────────────┬───────────────────────────────┬─────────────────────────────┘
                ▼                               ▼
          Postgres 16                    data/ticks/*.parquet
  (users, accounts, plans, locks,        (recorded ticks & candles,
   orders, trades, events, audit)         backed up off-server)
                                                │
                Kotak Neo API (orders from whitelisted static IP; live feed websocket)
```

### 4.2 Dependency rules (enforced with `import-linter` in CI)
- `domain` imports only the standard library and Pydantic.
- `ports` import only `domain`.
- `services` import `domain` and `ports`, never `adapters`.
- `adapters` import `domain` and `ports`.
- `api` imports `services` and `domain`. Wiring (which adapter implements which port) happens
  in one place: `backend/src/coolcandle/main.py`.

### 4.3 Extension points V1 must leave in place
| For | V1 provides | Later version adds |
|---|---|---|
| V2 | Tick recorder from day one; candle builder; `OrderRequest.source` field (`manual`/`signal`/`agent`/`trailing`); `SignalSource` port (no implementation) | Detectors, backtester, signal store |
| V3 | WebSocket event bus; `Notifier` port; `confirm_token` concept on order submit | Telegram notifier, agent with tools, voice |
| V4 | `OrderService.modify_stop(trade_id, new_trigger)` going through the guard; `TrailingPolicy` port | Trailing rules and the per-tick loop |

### 4.4 Runtime tasks inside the backend
| Task | Interval | Job |
|---|---|---|
| Market feed | push | Subscribe to Kotak websocket; fan out ticks to WS hub, candle builder, recorder, guard |
| Day monitor | every tick + every 1 s | Recompute `DaySnapshot`; trigger lock when a limit is hit; time-based exits |
| Order sync | Kotak order feed (push) + poll every 2 s as fallback | Update orders/trades from the broker |
| Reconcile | on login, on reconnect, every 5 s | Compare our trades/orders with broker positions/orders; fix or flag `NEEDS_ATTENTION` |
| Scheduler | cron-like | 08:30 IST scrip master download; 09:00 day reset; 15:15 square-off; 15:45 recorder flush to Parquet; nightly backup |

All tasks run with an injected `Clock`, so tests can replay a whole day in seconds.

---

## 5. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.12, **uv**, FastAPI, Pydantic v2, asyncio | Kotak SDK and V2 modelling are Python |
| DB | Postgres 16, SQLAlchemy 2 (async), Alembic | Migrations from the first commit |
| Tick storage | JSONL during the day → Parquet nightly (pyarrow / polars) | `data/ticks/YYYY-MM-DD/<instrument>.parquet` |
| Broker SDK | `neo_api_client` (Kotak Neo Trade API v2) wrapped in an adapter | Plus raw HTTP where the SDK is missing something |
| Auth | argon2 password hashes, `pyotp` for app TOTP, httpOnly secure session cookie | |
| Secrets | AES-GCM (or Fernet) with key from env `COOLCANDLE_SECRET_KEY` | Key never stored in the DB or repo |
| Frontend | React 18, Vite, TypeScript (strict), TanStack Query, TradingView **lightweight-charts** | CSS variables for theming |
| Tests | pytest, hypothesis (guard properties), pytest-asyncio; Vitest; Playwright e2e | |
| Quality | ruff, mypy `--strict`, import-linter; eslint/oxlint, `tsc --noEmit` | All in CI |
| Deploy | Docker Compose: `api`, `web` (static, served by Caddy), `postgres`, `caddy` (HTTPS) | India VM, static IP |
| CI | GitHub Actions: lint, types, tests, build images | |

---

## 6. Repository layout

```
coolcandle/
├── BLUEPRINT.md                 # this file
├── CLAUDE.md                    # short rules for coding agents (copy §0)
├── README.md                    # setup & run
├── docs/
│   ├── adr/                     # one file per architecture decision (0001-*.md …)
│   └── broker-notes/kotak-neo.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── src/coolcandle/
│   │   ├── main.py              # app factory + wiring of ports to adapters
│   │   ├── config.py            # pydantic-settings, env prefix COOLCANDLE_
│   │   ├── cli.py               # create-admin, fetch-scrips, replay-day, backup
│   │   ├── domain/
│   │   │   ├── models.py        # Instrument, Tick, Candle, OrderRequest, Order, Position, Funds …
│   │   │   ├── plan.py          # DailyPlan, plan edit rules (stricter-only)
│   │   │   ├── guard.py         # evaluate(plan, snapshot, request) -> Decision
│   │   │   ├── sizing.py        # lots from risk budget
│   │   │   ├── costs.py         # brokerage, STT, exchange, SEBI, stamp, GST estimate
│   │   │   ├── trade_state.py   # Trade state machine (pure transitions)
│   │   │   └── calendar.py      # trading days, holidays, session times (IST)
│   │   ├── ports/
│   │   │   ├── broker.py        # Broker protocol + BrokerSpec (fields for the connect form)
│   │   │   ├── market_data.py
│   │   │   ├── clock.py
│   │   │   ├── signal_source.py # V2 (interface only in V1)
│   │   │   ├── notifier.py      # V3 (interface only in V1)
│   │   │   └── trailing.py      # V4 (interface only in V1)
│   │   ├── adapters/
│   │   │   ├── brokers/kotak_neo/   # client.py, mapping.py, scrips.py, feed.py
│   │   │   ├── brokers/paper/       # fills against live quotes
│   │   │   ├── brokers/catalog.py   # Kotak live; Zerodha, Dhan, Angel One, Upstox coming soon
│   │   │   └── market/replay/       # plays recorded Parquet days through MarketData
│   │   ├── services/
│   │   │   ├── auth/            # users, password, app TOTP, sessions, lockout
│   │   │   ├── accounts/        # broker accounts, encrypted credentials, daily connect
│   │   │   ├── plans/           # draft → confirm x2 → locked; stricter edits
│   │   │   ├── guard/           # builds DaySnapshot, applies domain.guard, day locks
│   │   │   ├── orders/          # OrderService (the one door), trade manager
│   │   │   ├── market/          # feed manager, candle builder, option chain
│   │   │   ├── recorder/        # tick recording + nightly Parquet
│   │   │   ├── reconcile/
│   │   │   ├── journal/         # day summaries, trade timelines
│   │   │   └── scheduler/
│   │   ├── api/
│   │   │   ├── deps.py          # current_user, current_account, mode
│   │   │   ├── ws.py            # WebSocket hub
│   │   │   ├── schemas.py
│   │   │   └── routes/          # auth, brokers, plan, market, orders, trades, day, journal, settings
│   │   └── db/
│   │       ├── tables.py
│   │       ├── session.py
│   │       └── migrations/
│   └── tests/
│       ├── unit/                # domain: guard, sizing, costs, trade_state, calendar
│       ├── contract/            # one suite every Broker adapter must pass (paper first)
│       ├── replay/              # whole recorded days through the real services
│       └── api/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/                 # router, providers, theme
│       ├── lib/                 # api.ts, ws.ts, format.ts (₹, IST)
│       ├── components/          # shared UI
│       └── features/
│           ├── auth/  brokers/  plan/  trading/  lock/  journal/  settings/
├── deploy/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── backup.sh
├── data/                        # git-ignored: ticks, scrip masters
└── .github/workflows/ci.yml
```

---

## 7. Domain model

All models are Pydantic v2, `frozen=True`, `Decimal` for money and prices, UTC-aware datetimes.

| Model | Key fields | Notes |
|---|---|---|
| `Instrument` | `key` (e.g. `nse_fo\|48211`), `exchange_segment`, `trading_symbol`, `kind` (INDEX/OPTION/FUTURE/EQUITY), `underlying`, `expiry`, `strike`, `option_type` (CE/PE), `lot_size`, `tick_size`, `freeze_qty` | Option fields required iff `kind=OPTION`. Lot size always from the scrip master, never hard-coded. |
| `Tick` | `instrument_key`, `ts`, `received_at`, `ltp`, `bid?`, `ask?`, `volume_today?`, `oi?` | Carries the key, not the full instrument. |
| `Candle` | `instrument_key`, `interval`, `start`, `open/high/low/close`, `volume`, `oi?` | OHLC consistency validator. |
| `DailyPlan` | `trading_date`, `profit_target`, `loss_limit`, `max_trades`, `max_loss_per_trade`, `entry_cutoff` (14:30), `square_off` (15:15), `cool_off_minutes` (0), `allowed_underlyings`, `status` (DRAFT/CONFIRMING/LOCKED), `confirmations` | |
| `DaySnapshot` | `realised_pnl`, `unrealised_pnl`, `est_charges`, `net_pnl`, `peak_net_pnl`, `trades_taken`, `open_trade?`, `last_loss_at?`, `feed_age`, `now` | Built by the guard service. |
| `OrderRequest` | `instrument_key`, `side`, `qty`, `order_type` (LIMIT/SL/SL-M/MARKET), `price?`, `trigger?`, `product` (MIS), `source` (manual/signal/agent/trailing), `intent` (ENTRY/STOP/EXIT/MODIFY_STOP), `trade_id?`, `confirm_token?` | |
| `Decision` | `action` (ALLOW/WARN/BLOCK/LOCK), `reasons: list[Reason]`, `max_qty?` | Reasons are codes + human text for the UI. |
| `Order` | `id`, `broker_order_id`, `request`, `status`, `filled_qty`, `avg_price`, `reject_reason?`, `updated_at` | |
| `Position` | `instrument_key`, `net_qty`, `buy_avg`, `sell_avg`, `realised`, `unrealised`, `ltp` | From broker. |
| `Funds` | `available`, `used_margin`, `as_of` | |
| `Trade` | `id`, `state`, `instrument_key`, `qty`, `entry_order_id`, `stop_order_id`, `exit_order_id?`, `stop_trigger`, `target?`, `entry_avg?`, `exit_avg?`, `realised_pnl`, `source`, `opened_at`, `closed_at?`, `exit_reason?` | One idea from entry to exit. |

---

## 8. Database (Postgres)

Every table has `id` (UUID), `created_at`, `updated_at` unless noted. Money columns are
`NUMERIC(14,2)`, prices `NUMERIC(12,2)`.

| Table | Columns (besides the standard ones) | Notes |
|---|---|---|
| `users` | `email` (unique), `password_hash`, `totp_secret_enc`, `role` (admin/user), `is_active`, `failed_logins`, `locked_until` | Created by `cli create-admin`; no sign-up in V1. |
| `sessions` | `user_id`, `token_hash`, `expires_at`, `ip`, `user_agent` | Cookie holds the token; DB holds the hash. |
| `broker_accounts` | `user_id`, `broker` (`kotak_neo`), `label`, `credentials_enc` (JSON blob, encrypted), `status`, `last_connected_at` | Credentials for Kotak: consumer key, mobile, UCC, MPIN. Never the TOTP. |
| `broker_sessions` | `broker_account_id`, `trading_date`, `session_enc`, `expires_at` | Today's broker tokens, encrypted. |
| `daily_plans` | `broker_account_id`, `trading_date`, plan fields, `status`, `confirmations`, `locked_at` | Unique (`broker_account_id`, `trading_date`). |
| `plan_changes` | `plan_id`, `field`, `old`, `new`, `changed_at` | Stricter-only edits after lock; audit. |
| `day_locks` | `broker_account_id`, `trading_date`, `mode` (paper/live), `reason` (TARGET/LOSS_LIMIT/MAX_TRADES/SQUARE_OFF), `snapshot` (JSONB), `locked_at` | Unique (`broker_account_id`, `trading_date`, `mode`). **No API deletes this row.** |
| `orders` | `broker_account_id`, `mode`, `trade_id?`, `broker_order_id`, `request` (JSONB), `status`, `filled_qty`, `avg_price`, `reject_reason`, `external` (bool: placed outside CoolCandle) | |
| `trades` | `broker_account_id`, `mode`, trade fields from §7 | |
| `trade_events` | `trade_id`, `ts`, `type`, `data` (JSONB) | Append-only; the journal timeline. |
| `audit_log` | `user_id?`, `ts`, `action`, `data` (JSONB, secrets removed), `ip` | Sign-ins, mode switches, plan changes, orders, locks. |
| `instruments` | scrip master fields, `trading_date` | Reloaded daily. |
| `holidays` | `date`, `exchange`, `name` | Seeded yearly from NSE/BSE circulars. |
| `settings` | `user_id`, `key`, `value` (JSONB) | Theme, defaults for the plan form. |

---

## 9. The guard (core of V1)

### 9.1 Day plan lifecycle
```
NO_PLAN ──fill form──► DRAFT ──confirm #1 (summary dialog)──► CONFIRMING ──confirm #2 (type LOCK)──► LOCKED
                         ▲                                         │
                         └──────────── cancel ─────────────────────┘
LOCKED: frozen until next trading day. Stricter-only edits allowed (D2), each confirmed once.
```
- Orders require `status = LOCKED`.
- Validation: `loss_limit > 0`, `profit_target > 0`, `1 ≤ max_trades ≤ 20`,
  `max_loss_per_trade ≤ loss_limit`, `entry_cutoff < square_off ≤ 15:20`.
- "Stricter" means: lower `profit_target`, lower `loss_limit`, lower `max_trades`,
  lower `max_loss_per_trade`, earlier `entry_cutoff`/`square_off`, longer `cool_off`,
  smaller `allowed_underlyings`. Anything else is rejected with "The plan is locked for today".

### 9.2 What counts
- **Net P&L** = Σ over today's F&O positions in the broker account of (realised + unrealised)
  − estimated charges (`domain/costs.py`). Positions come from the broker, so trades placed in
  Kotak's own app count (D4). Unrealised is marked with live LTP between position polls.
- **Trades taken** = number of entry fills today: CoolCandle entries plus entries found in the
  broker order book that CoolCandle did not place (`orders.external = true`) (D4).
- Paper and live modes have separate snapshots and separate locks.

### 9.3 Day state and triggers
| State | Condition | Effect |
|---|---|---|
| `NO_PLAN` | no locked plan today | Charts on, ordering off |
| `ACTIVE` | plan locked, no limit hit | Ordering on |
| `WARNING` | net P&L ≤ −50% or −80% of loss limit, or 1 trade left | Banner; ordering on |
| `COOL_OFF` | within `cool_off_minutes` after a losing trade | New entries blocked, countdown shown |
| `ENTRIES_CLOSED` | after `entry_cutoff`, or `trades_taken = max_trades` with a trade still open | No new entries; open trade managed |
| `LOCKED` | net P&L ≥ profit target (D3), net P&L ≤ −loss limit, `max_trades` reached and no open trade, or `square_off` time | See 9.4 |

The day monitor evaluates triggers on every tick and every second.

### 9.4 What the lock does (in this order, idempotent)
1. Write the `day_locks` row (so a crash after this point still leaves the day locked).
2. Cancel all pending CoolCandle entry orders.
3. Exit every open CoolCandle trade by **modifying its stop order** into a marketable limit
   sell; if not filled in 2 s, re-price at the bid; escalate to `NEEDS_ATTENTION` after 10 s.
4. Push `day_status` over WebSocket; the UI greys out ordering with the reason.
5. Write audit + journal entries.

The lock ends only at **09:00 IST on the next trading day** (holiday calendar). There is no
unlock button and no API to delete a lock. Trades in the broker's own app cannot be blocked;
the locked screen shows a link to Kotak's own kill switch so the user can lock there too.

### 9.5 Checks before every order (`domain.guard.evaluate`)
For `intent = ENTRY`, every check must pass:
1. Plan is `LOCKED`, day not `LOCKED`, not `COOL_OFF`, not `ENTRIES_CLOSED`.
2. No other open trade (D11).
3. Underlying is in `allowed_underlyings`; instrument is an index option; side is BUY (D5); product MIS.
4. A stop is given and is below the entry price.
5. **Worst-case loss fits.** `worst = (entry − stop_limit_price) × qty + est_charges(qty) + slippage_buffer`;
   require `worst ≤ max_loss_per_trade` and `worst ≤ loss_limit + net_pnl` (what's left of today's budget).
6. Quantity is a whole number of lots and ≤ freeze quantity (slice if larger, later).
7. Feed is fresh (last tick < 3 s old) and the limit price is within a band of LTP (default ±3%).
8. Time is before `entry_cutoff`.
9. Live mode only: broker session valid, and today's live order count < hard cap (default 30).

For `intent = STOP / EXIT / MODIFY_STOP`: always allowed if it reduces risk (exits and tighter
stops are never blocked, even when locked). A `MODIFY_STOP` that **loosens** a stop is blocked.

`sizing.max_lots(plan, snapshot, entry, stop, lot_size)` returns the largest lot count that
passes check 5; the order ticket uses it as the default quantity.

### 9.6 Guard property tests (hypothesis)
- No sequence of allowed orders can make worst-case day loss exceed `loss_limit` (excluding
  slippage beyond the buffer).
- After `LOCKED`, `evaluate` never returns ALLOW for an ENTRY on that trading date.
- Exits are always allowed.
- Stricter-only edits can never loosen any field.

---

## 10. Orders and trades

### 10.1 `OrderService` (the one door)
```python
class OrderService:
    async def submit_entry(self, req: EntryRequest) -> Trade: ...   # guard → place LIMIT buy
    async def exit_trade(self, trade_id, reason) -> Trade: ...      # modify stop into exit
    async def modify_stop(self, trade_id, new_trigger) -> Trade: ... # V4 uses this
    async def cancel_entry(self, trade_id) -> Trade: ...
```

### 10.2 Trade state machine (`domain/trade_state.py`)
```
PROPOSED ─confirm─► ENTRY_PENDING ─fill─► OPEN ─exit trigger─► EXITING ─fill─► CLOSED
   │                    │   │                │
   │ expire/decline     │   └ partial fill ─►│ stop placed for filled qty; rest cancelled after 30 s
   ▼                    ▼                    ▼
 EXPIRED        REJECTED / CANCELLED   NEEDS_ATTENTION (unexpected broker state; alert, manual review)
```
`PROPOSED` is used by V2/V3; manual tickets go straight to `ENTRY_PENDING`.

### 10.3 Rules
- **Entry**: LIMIT buy at the price on screen (marketable limit, never MARKET on options).
  If the ask has moved beyond the band since the user clicked, refuse with the new price.
- **Stop**: placed the moment the entry fills, for the filled quantity, as **SL-limit**:
  trigger = user stop, limit = trigger − 2% (min 1 tick). If the trigger is crossed and the
  order is not filled in 2 s, modify to a marketable limit at the bid.
- **Target**: tracked by CoolCandle (not a resting order). When LTP ≥ target, **modify the
  stop order** into a limit sell at the bid. Never cancel the stop first.
- **Before any exit**, read the real position quantity so stop and exit can never both fill.
- **Exit reasons**: STOP, TARGET, MANUAL, DAY_LOCK, SQUARE_OFF, TRAILING (V4).
- Every transition writes a `trade_events` row.

### 10.4 Reconciliation
On login, reconnect, and every 5 s: compare open trades and resting orders with broker
positions and the order book. Missing stop for an open position → place it immediately and
alert. Position closed at the broker → close the trade with the broker's fill. Anything else
unexpected → `NEEDS_ATTENTION`, block new entries, show a banner.

---

## 11. Brokers

### 11.1 Port
```python
class Broker(Protocol):
    spec: BrokerSpec                      # id, name, status (live/coming_soon), credential fields
    async def connect(self, creds: dict, totp: str) -> BrokerSession: ...
    async def funds(self) -> Funds: ...
    async def positions(self) -> list[Position]: ...
    async def orders(self) -> list[Order]: ...
    async def place_order(self, req: OrderRequest) -> Order: ...
    async def modify_order(self, broker_order_id: str, changes: OrderChanges) -> Order: ...
    async def cancel_order(self, broker_order_id: str) -> Order: ...
    def order_updates(self) -> AsyncIterator[Order]: ...
    async def quotes(self, keys: list[str]) -> list[Tick]: ...
```
`BrokerSpec.fields` describes the connect form (name, label, type, secret: bool), so the UI
builds the form dynamically. Every adapter passes `tests/contract/`; the paper adapter first.

### 11.2 Catalog
| Broker | Status | Fields |
|---|---|---|
| Kotak Neo | **Live (V1)** | Consumer key, mobile number, UCC, MPIN · daily: TOTP |
| Zerodha (Kite Connect) | Coming soon | API key, API secret · daily: login redirect |
| Dhan | Coming soon | Client ID, access token |
| Angel One (SmartAPI) | Coming soon | API key, client code, PIN · daily: TOTP |
| Upstox | Coming soon | API key, API secret · daily: login redirect |

### 11.3 Paper broker
Same interface. Fills LIMIT buys when ask ≤ limit, SL-limits when the trigger trades and bid ≥
limit, using live quotes; applies the same cost model. Paper is the default mode at every login.

### 11.4 Kotak Neo notes (verified on the old project, Oct 2026; re-verify in M0)
- SDK `neo_api_client` (package `kotakneoapi`). Login: `totp_login(mobile, ucc, totp)` →
  `totp_validate(mpin)`. The response gives a per-account `baseUrl`; all later calls use it.
- Raw login: `POST https://mis.kotaksecurities.com/login/1.0/tradeApiLogin` then `.../tradeApiValidate`.
- Quotes need only the consumer key: `GET {baseUrl}/script-details/1.0/quotes/neosymbol/{segment}|{token}/all`.
  Index by name: `nse_cm|Nifty 50`, `bse_cm|SENSEX`.
- **Every number arrives as a string**; parse to `Decimal`. Zero for index volume/OI/depth means "not applicable" → `None`.
- Live feed: indices via `subscribe_index()`, options via `subscribe_scrips()`; NIFTY ~8–10 msgs/s.
  Prices arrive as float → `Decimal(str(x))`. `volume_traded_today` is cumulative. Messages repeat; dedupe.
- Order feed: `create_order_feed()` → order/position updates. The SDK does **not** retry orders; we decide.
- Scrip master: `GET {baseUrl}/script-details/1.0/masterscrip/file-paths` → daily CSVs per segment.
  In `nse_fo.csv`: token `pSymbol`, symbol `pTrdSymbol`, underlying `pSymbolName`, type `pInstType`
  (`OPTIDX`). **Expiry is seconds since 1980-01-01** (add 315,532,800 for Unix). **Strike and tick
  size are in paise.** Column names have trailing spaces/semicolons; strip them.
- History: `historical_data(...)`; strict rate limit (3 quick calls → 429): pause 2 s, back off.
  Index 1-minute volume is always 0. **Expired option contracts have no history**, hence the recorder.
- Funds: `POST {baseUrl}/quick/user/limits` with `jData={"seg":"ALL","exch":"ALL","prod":"ALL"}`.
- Two logins with the same account end each other's session: never run two instances.
- **API orders must come from the static IP whitelisted in Kotak's Trade API settings.**

---

## 12. Market data and recording
- `FeedManager` subscribes to: NIFTY and SENSEX indices, plus ATM ± 10 strikes (CE and PE) of
  the nearest weekly expiry, re-centred when spot moves 2 strikes. Respect the broker's
  subscription cap.
- `CandleBuilder` builds 1-minute and 5-minute candles from ticks; 1-year history is backfilled
  once from Kotak for the indices.
- `Recorder` appends every tick to `data/ticks/YYYY-MM-DD/*.jsonl`; at 15:45 converts to Parquet
  and uploads a copy off-server (S3-compatible bucket). This dataset is V2's training data.
- Option chain endpoint returns strikes around ATM with LTP, bid/ask, OI, change.
- Stale feed (> 3 s without ticks during market hours) → new entries blocked, banner shown.

---

## 13. Auth and security
- **Sign-in (D1):** email + password (argon2) + 6-digit authenticator code (`pyotp`). 5 failures
  → locked 15 minutes. Session cookie: httpOnly, Secure, SameSite=Strict, 12-hour expiry.
- **Admin:** created with `uv run coolcandle create-admin`. Sign-up endpoint does not exist in V1.
- **Broker connect:** each morning the user enters the broker TOTP; MPIN and keys come from the
  encrypted credentials. TOTP is used once and never stored.
- **Encryption:** credentials and broker session tokens encrypted with `COOLCANDLE_SECRET_KEY`
  (env only). A copy of the DB alone reveals nothing.
- **Transport:** HTTPS only (Caddy). CORS locked to the app's origin. CSRF token on mutating requests.
- **Logging:** a log filter removes known secret fields; tests assert no secret appears in logs.
- **Audit log** for every sign-in, broker connect, mode switch, plan change, order and lock.
- Hosting account and GitHub protected with 2FA.

---

## 14. API

REST under `/api`, JSON, cookie auth. Errors: `{ "code": "PLAN_LOCKED", "message": "…" }`.

| Method & path | Purpose |
|---|---|
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | Sign-in (password + TOTP) |
| `GET /brokers` | Catalog with status and form fields |
| `POST /broker-accounts` · `GET /broker-accounts` · `DELETE /broker-accounts/{id}` | Save/list/remove encrypted credentials |
| `POST /broker-accounts/{id}/connect` | Daily connect with TOTP |
| `POST /broker-accounts/{id}/test` | Check connection and whitelisted IP |
| `GET /plan/today` · `PUT /plan/today` | Read / edit draft (or stricter-only edit) |
| `POST /plan/today/confirm` | Body `{step: 1}` then `{step: 2, typed: "LOCK"}` |
| `GET /day/status` | Day state, snapshot, reasons, lock |
| `POST /mode` | `{mode: "paper" \| "live", typed: "LIVE"}`; refused with an open trade |
| `GET /market/candles` · `GET /market/option-chain` · `GET /market/instruments` | Data |
| `POST /orders/preview` | Guard decision + max lots + costs for a ticket, without placing |
| `POST /trades` | Submit entry (instrument, limit, stop, target?, lots) |
| `POST /trades/{id}/exit` · `POST /trades/{id}/cancel` | Manual exit / cancel pending entry |
| `GET /trades?date=` · `GET /trades/{id}` | List / timeline |
| `GET /journal?from=&to=` | Day summaries |
| `GET/PUT /settings` | Theme, plan form defaults |

**WebSocket `/ws`** (one connection, server push; client sends `subscribe` for instruments):
`tick`, `candle`, `day_status`, `order_update`, `trade_update`, `alert`, `feed_status`.

---

## 15. UI

| Screen | Contents |
|---|---|
| Sign-in | Email, password, authenticator code |
| Brokers | Catalog cards (Kotak live; others "Coming soon"); dynamic form; test connection; status (connected, session expired, IP not whitelisted) |
| Morning connect | Broker TOTP field; shows funds after connect |
| Today's plan | Form with ₹ fields and times; step 1 summary dialog; step 2 type `LOCK`; locked view with "make stricter" |
| Trading | Index chart (1m/5m), option chain, order ticket (lots defaulted from sizing, stop required, target optional, preview of worst-case loss and charges), open trade card with live P&L, stop, target, Exit button |
| Always-on header | Mode badge (PAPER / LIVE), day P&L meter between −loss limit and +target, trades left, feed age, day state |
| Locked | Ordering greyed out, reason ("You reached today's loss limit. Trading opens again tomorrow at 09:15."), day summary, link to Kotak's kill switch |
| Journal | Calendar of days with P&L and lock reason; trade timelines |
| Settings | Theme, plan defaults, sessions, sign out everywhere |

- **Theme:** dark and light, switched with a **1-second cross-fade** (View Transitions API, CSS
  transition fallback; disabled under `prefers-reduced-motion`).
- Numbers in tabular figures; ₹ with Indian grouping (₹1,00,000); times in IST.
- The locked state is driven by `day_status` from the server, never by client logic alone.

---

## 16. Later versions in detail

### 16.1 V4 Trailing stop (built second)
- `TrailingPolicy.next_stop(trade, candles, ltp) -> Decimal | None`, pure.
- Rules offered: breakeven after X points; at trade target switch to trailing with stop at
  target − Y; step trail every N points; swing-low trail on 1-minute candles; ATR trail.
- Each move calls `OrderService.modify_stop`, which only allows tightening. Rate limit: at most
  one modification per trade per 2 s.
- Day-target interaction (D3 later): at the day target, block new entries but let the open trade
  trail with a floor that protects at least the day target minus a give-back percentage.

### 16.2 V2 Decision model
- Encode Aman's setups as precise rules: how a support/resistance level is found, what counts as a
  breakout (close beyond level by X, volume/OI confirmation from options since index volume is 0),
  invalidation, entry, stop, target, valid-until time.
- Each setup is a `SignalSource` producing `Signal(instrument, entry, stop, target, expires_at, reason, features)`.
- Backtester replays recorded days through the real guard and paper broker with full costs and slippage.
- **Gate (D7):** positive average net P&L per trade after costs, profit factor ≥ 1.3, over ≥ 150–200
  trades, on walk-forward data the setup was not tuned on, with a worst losing streak the daily loss
  limit absorbs; then one month of paper signals that match the backtest.
- Why not "80–90% accuracy": win rate alone doesn't decide profit. Break-even win rate ≈
  stop ÷ (stop + target) plus costs. With a 12-point target and 12-point stop you need just over 50%;
  with a 36-point stop you need about 75%. Small targets also lose 1.5–3 points to spread, slippage
  and charges. Backtests showing 85%+ usually mean overfitting or look-ahead bugs.
- ML (gradient boosting) may later rank or filter setups. An LLM reading chart images can label and
  explain, not predict.
- Every experiment is logged (setup version, data range, metrics) so results stay comparable.

### 16.3 V3 Alerts and voice buddy
- `Notifier` adapters: Telegram bot (owner's chat only), web push, in-app. SMS last (needs DLT registration).
- Alert text: "NIFTY 24500 CE ~85 · stop 62 · target 120 · valid till 10:42" + signed, expiring link to a confirm screen.
- Agent: Claude with typed tools only: `get_day_status`, `get_plan`, `get_current_signal`,
  `propose_trade`, `confirm_trade(confirm_token)`, `exit_trade`. The confirm token is created only by
  a tap in the UI (D6). All tools go through `OrderService`; the agent refuses after a lock.
- Text chat first, then voice (speech-to-text → agent → text-to-speech), English and Hindi.
- Signals carry a price band and expiry; an order is refused if price has left the band.
- Eval suite of 30+ scripted conversations in CI: refuses when locked, never orders without a tap,
  never states a price that isn't from the signal, handles "skip today".

---

## 17. Testing
| Layer | What | Tool |
|---|---|---|
| Unit | guard, sizing, costs, plan edits, trade state, calendar | pytest + hypothesis |
| Contract | every Broker adapter passes the same suite | pytest (paper always; Kotak behind `--kotak` flag, read-only calls) |
| Replay | full recorded days through real services with a fake clock: each limit triggers once, restart mid-day keeps the lock, reconcile fixes a missing stop | pytest |
| API | auth, plan flow, error codes, no secrets in responses/logs | pytest + httpx |
| Frontend | components and day-state rendering | Vitest |
| E2E | sign-in → plan → paper trade → lock → greyed UI | Playwright |

CI blocks merge on: ruff, mypy strict, import-linter, pytest, tsc, lint, Vitest, build.

---

## 18. Deployment and operations
- **Server:** small VM in Mumbai or Bangalore (2 vCPU, 4 GB) with a **static IP**, whitelisted in
  Kotak's Trade API settings. Docker Compose with `caddy`, `api`, `postgres`. Domain with HTTPS.
- **Backups:** nightly `pg_dump` + Parquet ticks to an off-server bucket; restore tested once a month.
- **Monitoring:** health endpoint; alert to Aman (email, then Telegram in V3) on feed down, broker
  session expired, `NEEDS_ATTENTION`, backup failure.
- **Daily routine:** 08:30 scrip master download → 09:00 sign in and connect broker → set and lock
  plan → trade → 15:15 square-off → 15:45 recorder flush → review journal.
- **Never run two instances** against the same broker account.
- Config via env (`COOLCANDLE_` prefix): DB URL, secret key, allowed origin, timezone, live order cap.

---

## 19. V1 milestones

| # | Milestone | Scope | Done when |
|---|---|---|---|
| M0 | Foundations | Repo layout (§6), CI, Docker Compose, Postgres + Alembic, VM with static IP, `CLAUDE.md`. Script proving Kotak login, quotes, a far-from-market limit order and its cancel from the server. | A test order placed from the server is accepted by Kotak and cancelled. |
| M1 | Sign-in and broker connect | Users, app TOTP, sessions, audit log; broker catalog and dynamic form; encrypted credentials; daily connect; funds shown. | Aman signs in and connects Kotak from the browser; no secret in logs (test). |
| M2 | Live market data | Feed manager, WebSocket hub, candles, chart, option chain, recorder, history backfill. | A full market day watched live and recorded without gaps. |
| M3 | Plan and guard | Plan lifecycle with two confirmations and stricter-only edits; `domain.guard` with property tests; day monitor; day locks; header meter; locked screen. | Replayed days hit each limit correctly; restart mid-day keeps plan and lock. |
| M4 | Paper trading | `OrderService`, trade state machine, paper broker, order ticket with preview, exits, square-off, journal. | A week of paper sessions with correct P&L, counts and locks. |
| M5 | Live orders | Kotak adapter for orders and order feed; SL-limit with escalation; reconcile; mode switch; live order cap; external trades counted. | A week live at one lot with zero mismatches between CoolCandle and Kotak. |
| M6 | Polish | Theme fade, journal calendar, error alerts, backups, docs, ADRs. | V1 "done" per §3. |

---

## 20. Regulatory notes (India)
- SEBI's retail algo framework (circular of Feb 2025, rolled out by exchanges through 2025–26):
  API orders only via the broker, from a **whitelisted static IP**, tagged by the broker; strategies
  above the order-rate threshold (about 10 orders/second per exchange) must be registered with the
  exchange through the broker. A personal tool with a human confirming each entry and low order rates
  is the lightest case. **Confirm with Kotak before automatic exits/trailing go live.**
- Sending trade recommendations to anyone else requires SEBI Research Analyst registration. V3
  alerts go only to the account owner.
- Offering CoolCandle to other users, or placing trades for them, needs legal advice first.
- Charges (STT, exchange, SEBI fee, stamp duty, GST, brokerage) are **config values**, not constants;
  verify current rates before going live.

---

## 21. Out of scope for V1
Signals, AI, voice, Telegram, trailing, multiple brokers live, multiple users, option selling,
futures, equity, overnight positions, mobile app, syncing/showing every order made in Kotak's own
app (only counted, D4), order slicing above freeze quantity.

---

## 22. Glossary
| Term | Meaning |
|---|---|
| CE / PE | Call / put option |
| ATM | At-the-money strike, closest to spot |
| MIS | Intraday product; auto-squared off by the broker |
| SL-limit / SL-M | Stop order with a limit price / at market |
| LTP | Last traded price |
| Premium points | Change in the option's price (not the index's) |
| Freeze quantity | Max quantity per order the exchange allows |
| UCC | Unique client code at the broker |
| MPIN | Broker's 4–6 digit PIN |
| TOTP | Time-based one-time code from an authenticator app |
| Day lock | CoolCandle's block on new entries until the next trading day |
| Walk-forward | Testing on data after the period a strategy was tuned on |
