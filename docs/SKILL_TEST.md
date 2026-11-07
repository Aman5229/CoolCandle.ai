# Skill Test: build spec

> A practice arena inside CoolCandle.ai. Real Binance prices, fake money, the same discipline
> rules as live, and a score for how well you kept them. Written 7 Nov 2026 for Claude Code to
> build. Read `BLUEPRINT.md` §0 (rules for anyone writing code here) first. Everything there still
> applies: Decimal money, UTC datetimes, the import-linter layers, tests before merge.

---

## 0. Read this first

1. **Skill Test can never touch live trading.** No Skill Test code path may reach a Kotak broker,
   the live `BrokerRouter` entry, the live `daily_plans`, `day_locks` or `trades` rows, or the
   live day P&L. This is the one rule that must not break. §4.4 lists the tests that enforce it.
2. **It is not the existing PAPER mode.** PAPER (the header switch) rehearses the live day: Kotak
   quotes, the live plan, reset at 09:00 IST. It stays exactly as it is. Skill Test is a separate
   world with its own account, its own fake funds that persist across days, its own markets
   (Binance) and its own screens.
3. **Reuse, don't fork.** Fills come from `PaperBroker`, money maths from `domain/costs.py` and
   `domain/sizing.py`, the trade lifecycle from `domain/trade_state.py`, the chart from
   `PriceChart.tsx` (Lightweight Charts v5). Where a live rule doesn't fit (IST hours, options
   only, buy only), add a parameter or a small Skill Test rule module. Don't copy the live guard.
4. **Discipline first, P&L second.** Every screen ranks the discipline score above profit.
5. Items marked **DEFAULT** in §2 are my picks. Build them that way, keep them easy to change,
   and record Aman's final answer in §2.

---

## 1. What the user gets

- A **Skill Test account** with fake funds (₹50,000 / ₹1,00,000 / ₹5,00,000 to start).
- **Live arena:** BTC, ETH and SOL live from Binance. Open 24/7, so you can practise at night
  and on weekends.
- **Replay arena:** pick a past day and a start time, then play it at 1x / 5x / 20x / 60x with
  pause. Future candles stay hidden until they play.
- **Sessions with a mini plan:** a profit target, a loss limit, max trades, a cool-off after a
  loss, and a duration. The plan is confirmed twice, as live is. When a limit hits, the session
  locks, just as the live day does.
- **Discipline score (0 to 100)** live in the HUD, and a **report card** at the end of every
  session.
- **Blown account:** if the fund falls 30% below its start, the run ends. You start a new run and
  the old one stays in history.
- A **different look**: an arcade/HUD theme, so it can never be mistaken for live money.

Phase 2 (§9) adds challenges, streaks, badges, the AI coach, a "ready for live" gate and
leaderboards. The MVP must leave room for them but not build them.

---

## 2. Decisions

| # | Question | Answer | Status |
|---|---|---|---|
| S1 | Data source | **Binance public market data**: websocket for live, REST and `data.binance.vision` for history. No API key. | **CONFIRMED** (Aman, 7 Nov 2026) |
| S2 | Account currency | Fake funds in **₹**. Binance prices are in USDT and converted at a fixed rate `skill_usdt_inr` (default 85), snapshotted on each session. | DEFAULT |
| S3 | Long and short | **Both allowed** (crypto has no CE/PE). A stop is still required on every entry. | DEFAULT |
| S4 | Leverage / margin | Margin = notional ÷ **5** (fixed 5x). A position can't open if margin exceeds free fake funds. | DEFAULT |
| S5 | Fees | **0.10% of notional per side** plus a slippage of 1 tick on market orders. Shown in the preview, as live charges are. | DEFAULT |
| S6 | Open while the live day is locked? | **Yes, with a banner** ("Live is locked for today. This is practice only."). Setting `skill_when_live_locked` can switch it off. | DEFAULT |
| S7 | Trades at once | **One open trade per session**, matching live D11. | DEFAULT |
| S8 | Blown threshold | Run ends at **−30%** of its starting fund. | DEFAULT |
| S9 | Session plan edits | Confirmed twice (summary, then type `START`). After that it can only be made **stricter**, reusing `domain/plan.stricter_changes`. | DEFAULT |
| S10 | Instruments | `BTCUSDT`, `ETHUSDT`, `SOLUSDT` (config list `skill_symbols`). | DEFAULT |

---

## 3. Data: Binance

### 3.1 Live

- Websocket base `wss://data-stream.binance.vision` (market-data-only host), with
  `wss://stream.binance.com:9443` as fallback. Use a combined stream:
  `/stream?streams=btcusdt@bookTicker/btcusdt@trade/btcusdt@kline_1m/...`.
- `@bookTicker` gives bid/ask (market orders fill against it), `@trade` gives LTP, and
  `@kline_1m` gives closed candles for the chart.
- Binance sends a ping every 3 minutes, and the client must pong. Connections are cut at 24 h,
  so reconnect with backoff and resubscribe. Report feed age the way the Kotak feed does, and
  block new Skill Test entries when the feed is stale (reuse `GuardConfig.feed_max_age_s`).
- **Region:** binance.com returns HTTP 451 from some locations (US cloud regions in particular).
  The India VM (D10) should be fine. The adapter must raise a clear "Binance is not reachable
  from this server" error instead of failing silently, and CI must never call Binance (use
  recorded fixtures).

### 3.2 History (replay and chart backfill)

- REST `GET https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1s|1m&startTime=&endTime=&limit=1000`.
  Weight is about 2 per call against a 6,000/minute IP limit. Throttle to 10 calls a second and
  honour `Retry-After` on 429/418.
- Bulk files: `https://data.binance.vision/data/spot/daily/klines/<SYMBOL>/<interval>/<SYMBOL>-<interval>-<YYYY-MM-DD>.zip`
  (CSV, with a `.CHECKSUM` file next to it; verify it). Use these for whole-day replays.
  Note that from 2025 the open-time column in spot files is in **microseconds**, not
  milliseconds. Detect this by size and handle both.
- Cache downloaded days as Parquet under `data/skill/klines/<symbol>/<interval>/<date>.parquet`.
  It's the same store style as the recorder, but a separate folder.
- Replay uses **1-second klines** when the day has them, and falls back to 1-minute.

### 3.3 Licensing

Public Binance market data is free to use for this personal app. Showing it to *other* users of
a commercial product needs a check of Binance's terms first. Note it in §20 of the blueprint;
nothing to build. TradingView widgets are **not** used, because they can't feed prices to the
backend.

### 3.4 Adapter

`adapters/market/binance/` implements the existing `MarketData` port:

- `is_real = False`. These prices are real, but they are not a market CoolCandle can trade live,
  so every live order path refuses them.
- `instruments()` returns one `Instrument` per symbol, with `kind = InstrumentKind.CRYPTO` (new
  enum value), `exchange_segment = "BINANCE"`, `tick_size` from `exchangeInfo` (BTC 0.01),
  `lot_size = 1`, and a new `qty_step` (BTC 0.001, ETH 0.01, SOL 0.1).
- `ticks()` maps bookTicker and trade messages to `Tick` (ltp, bid, ask).
- `history()` is backed by the kline cache in §3.2.
- `BinanceReplay` (same port) plays cached klines through a `ManualClock` at the chosen speed.
  It turns each kline into ticks along O → H → L → C for an up candle and O → L → H → C for a
  down candle. **If a stop and a target both sit inside one 1-minute candle, the stop fills
  first** (the conservative choice). Test it.

---

## 4. Backend

### 4.1 Module layout (follows the existing layers)

```
domain/skill/
  rules.py        # pure: Skill Test pre-order checks and session state (§4.3)
  score.py        # pure: discipline score (§6)
  report.py       # pure: report-card stats (§7)
  run.py          # pure: fund, blown check, run lifecycle
adapters/market/binance/
  feed.py  history.py  replay.py  mapping.py
services/skill/
  service.py      # SkillService: accounts, runs, sessions, orders (the one Skill Test door)
  arena.py        # one running arena per session: market + PaperBroker + clock + monitor
  store.py        # DB access for skill_* tables
api/routes/skill.py   # prefix /skill (mounted under /api)
```

Add import-linter contracts:

- `coolcandle.services.skill` must not import `coolcandle.services.orders`,
  `coolcandle.services.guard`, `coolcandle.services.reconcile`, `coolcandle.services.trailing`
  or `coolcandle.adapters.brokers.kotak_neo`.
- No live service may import `coolcandle.services.skill`.

`SkillService` gets its own `PaperBroker` instance per session, built by a factory in `main.py`.
It never goes through `BrokerRouter`.

### 4.2 Database (new Alembic migration, all tables prefixed `skill_`)

| Table | Key columns |
|---|---|
| `skill_accounts` | id, user_id (unique), created_at, settings (json) |
| `skill_runs` | id, account_id, number, start_fund, cash, status (`ACTIVE`/`BLOWN`/`RETIRED`), started_at, ended_at, peak_equity |
| `skill_sessions` | id, run_id, arena (`LIVE`/`REPLAY`), symbol, replay_date, replay_start, speed, usdt_inr, plan (json), plan_status, confirmations, state, lock_reason, started_at, ends_at, ended_at, start_equity, end_equity, score, score_breakdown (json) |
| `skill_trades` | same columns as `trades` minus `broker_account_id`/`mode`/`trading_date`, plus `session_id`, `side` (`LONG`/`SHORT`), `r_risk` (₹ risk at entry), `stop_widened` (bool) |
| `skill_orders` | id, session_id, trade_id, broker_order_id, request (json), status, filled_qty, avg_price, ts |
| `skill_events` | id, session_id, ts, kind, payload (json). This is the behaviour log the score is computed from (§6). |

Money is `Money`/`Price`, as in `db/tables.py`. Index `skill_trades(session_id)` and
`skill_events(session_id, ts)`.

### 4.3 Rules (`domain/skill/rules.py`)

A session plan has: `profit_target`, `loss_limit`, `max_trades`, `max_loss_per_trade`,
`cool_off_minutes`, `min_reward_risk`, `duration_minutes` (30/60/120/240). It is validated with
the same rules as `DailyPlan._limits_fit` (for example, the loss limit must fit inside the run's
free funds).

Session states: `DRAFT → ACTIVE → (WARNING | COOL_OFF) → LOCKED → ENDED`. The lock reasons
reuse `LockReason` (TARGET, LOSS_LIMIT, MAX_TRADES) plus `TIME_UP` and `BLOWN`.

Pre-order checks, each returning a `Reason` the UI can show (reuse `Decision`/`Reason`):

1. The session is ACTIVE (not draft, cool-off, locked or ended), and the run is ACTIVE.
2. The feed is fresh (live arena) or the replay is playing, not paused.
3. No open trade (S7).
4. A stop is present, on the correct side of entry, and at least `min_stop_pct` away.
5. Worst-case loss (with fees and slippage) ≤ `max_loss_per_trade` and ≤ the loss left today.
   Reuse `domain/sizing.worst_case_loss` with the S5 fee rates.
6. Reward to risk ≥ `min_reward_risk` when a target is set.
7. Margin (S4) ≤ free fake cash.
8. Trades taken < `max_trades`.

The **IST windows, options-only and buy-only rules from the live guard don't apply here.**
Don't change `domain/guard.py` for Skill Test. Factor out only what both sides share (for
example a `stop_side_ok(side, entry, stop)` helper), and keep the live tests green.

A **blocked attempt is logged** to `skill_events` (`BLOCKED`, with the reason code). The live
guard silently prevents; Skill Test also *counts*, because trying to trade after a lock is
exactly the habit we want to see.

### 4.4 Paper fills

Extend `PaperBroker`, which is shared with live PAPER, so keep its current behaviour:

- Short entries and **buy-side stops** (trigger above LTP for a short). Today
  `place_order` rejects any SL with a trigger ≥ LTP. Make the check depend on the side.
- Market orders fill at ask (buy) or bid (sell) ± S5 slippage. Limit orders fill when the price
  crosses them.
- Fees from S5 go into cash.
- Fractional quantity, using `qty_step`. `qty` stays an int of *steps* in `OrderRequest`; the
  adapter multiplies. That keeps `OrderRequest.qty: int` unchanged for live.

**Isolation tests (must pass):**
- Placing, modifying and exiting a Skill Test trade leaves `trades`, `orders`, `day_locks` and
  `daily_plans` untouched (row counts before = after).
- A Skill Test lock doesn't change live `day_status`, and a live lock doesn't lock Skill Test
  (S6 only shows a banner).
- `SkillService` with a Kotak broker registered as live still uses only its own `PaperBroker`
  (a spy asserts the Kotak broker gets zero calls).
- A Binance `Instrument` passed to the live `OrderService` is refused (`is_real = False` and an
  unknown instrument).

### 4.5 Arena runtime

- Live arena: one shared Binance feed task for all sessions, fanned out on the bus as
  `skill.tick` and `skill.candle`, separate from the live `tick`/`candle` topics.
- Replay arena: one task per session with its own `ManualClock`. Speed changes and pause are
  commands to it. On server restart, a replay session comes back **paused** at its last tick
  time (store `replay_cursor` on the session every 5 s).
- A monitor runs on every tick: recompute session P&L, and lock when target / loss / max trades /
  duration is hit. On lock, exit any open trade at market (live D3 behaviour), write
  `LOCKED`, and compute the score and report.
- Blown check after every exit: if equity < 70% of `start_fund`, set the run to `BLOWN`, end the
  session, and emit `skill.run_blown`.

### 4.6 API (`/api/skill`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/skill/account` | Account, active run, fund, equity, lifetime stats |
| POST | `/skill/runs` | Start a new run `{start_fund}` (only when there's no ACTIVE run, or after confirm `RETIRE`) |
| GET | `/skill/runs` | Run history |
| GET | `/skill/replay/days?symbol=` | Days available to replay (cached plus fetchable range) |
| POST | `/skill/sessions` | Create a draft `{arena, symbol, replay_date?, replay_start?, plan}` |
| POST | `/skill/sessions/{id}/confirm` | Step 1, then step 2 with `typed: "START"` |
| PATCH | `/skill/sessions/{id}/plan` | Stricter-only changes |
| POST | `/skill/sessions/{id}/replay` | `{action: play|pause|speed, speed?}` |
| POST | `/skill/sessions/{id}/preview` | Same shape as the live preview: worst-case loss, fees, margin, R:R, decision |
| POST | `/skill/sessions/{id}/orders` | Entry `{side, qty, order_type, price?, stop, target?}` |
| PATCH | `/skill/trades/{id}/stop` | Move the stop (widening is allowed but logged, §6) |
| POST | `/skill/trades/{id}/exit` | Exit at market |
| POST | `/skill/sessions/{id}/end` | End early (counts as quitting, no penalty) |
| GET | `/skill/sessions/{id}` | State, plan, trades, live score |
| GET | `/skill/sessions/{id}/report` | Report card (§7) |
| GET | `/skill/market/{symbol}/candles?interval=&from=&to=` | Chart data; for replay, never past the replay cursor |

WS messages on the existing socket: `skill.tick`, `skill.candle`, `skill.session` (state, P&L,
score), `skill.trade`, `skill.locked`, `skill.run_blown`, `skill.replay` (cursor, speed,
paused).

**Replay must never leak the future:** candles and ticks after the cursor are never sent,
including on reload. Test it.

---

## 5. Frontend

### 5.1 Entry and separation

- A new top-level view `'skill'` in `App.tsx` (`View` type), with a nav item **Skill Test**
  (icon: a target or trophy).
- On entering, set `document.documentElement.dataset.arena = "skill"`. All Skill Test colours
  come from tokens under `[data-arena="skill"]` in a new `features/skill/skill.css`, with dark and
  light variants. Use the existing 1-second view-transition cross-fade for the switch (and none
  under `prefers-reduced-motion`).
- The live header (mode badge, day meter) is **replaced** by the Skill HUD while in this view.
  Live funds, live P&L and the live order ticket are never rendered inside it.
- A persistent ribbon: **"SKILL TEST · FAKE MONEY"**.

### 5.2 Screens (`features/skill/`)

| Screen | Contents |
|---|---|
| Lobby | Fund card (equity, run number, blown line), "Start live session" and "Replay a day" tiles, last 5 session scores, lifetime best score |
| Session setup | Arena, symbol, (replay) date picker limited to available days and start time, plan form, then the summary dialog, then type `START` |
| Arena | HUD bar (fund, session P&L meter between −loss limit and +target, trades left, live score with ▲▼ flash on change, timer or replay clock), chart, order ticket, open-trade card, replay controls |
| Lock screen | Full-screen overlay: "Target hit 🎯" (confetti), "Loss limit reached", "Max trades used", "Time's up". Shows the score and a "See report card" button. The ticket stays greyed until a new session starts. |
| Blown screen | "Account blown", run summary, "Start a new run" |
| Report card | §7 |
| History | Runs → sessions → report cards |

### 5.3 Chart and ticket

- Reuse `PriceChart.tsx`, adding: entry, stop and target **price lines** that can be dragged
  (dragging the stop calls `PATCH /stop`), and trade markers via `createSeriesMarkers` (entry
  ▲/▼, exit ●, colour by win/loss).
- Ticket: **BUY / SELL** toggle (S3), quantity in coin units with the step from the instrument,
  stop required (the ticket can't submit without it), optional target, and a live preview from
  `/preview` (worst-case ₹ loss, fees, margin, R:R). Blocked reasons are shown inline, as live.
- Replay controls: play/pause, 1x/5x/20x/60x, cursor time. There's no rewind (it would let the
  user peek at the future).

### 5.4 Feel

- Motion stays short and purposeful: number tick-ups on P&L, a score pulse on change, confetti
  only on TARGET lock. All of it is disabled under `prefers-reduced-motion`.
- Numbers use tabular figures and ₹ with Indian grouping (reuse `lib/format.ts`). Show USDT
  prices on the chart and ₹ for money.

---

## 6. Discipline score (`domain/skill/score.py`, pure)

Each session starts at **100**. Penalties come from `skill_events` and `skill_trades`, and the
score floors at 0. The weights live in one table in code, so they're easy to tune.

| Behaviour | Detection | Penalty |
|---|---|---|
| Revenge entry | Entry within 3 minutes of a losing exit (even if cool-off is 0) | −10 each |
| Stop widened | Stop moved further from entry than at fill (`stop_widened`) | −15 each |
| Loss past 1R | Trade closed for a loss larger than its `r_risk` × 1.1 | −10 each |
| Size up after a loss | Risk on the next trade > 1.5 × risk of the losing trade | −10 each |
| Blocked attempt | `BLOCKED` event (after lock, oversize, no stop, cool-off) | −5 each, max −25 |
| Early cut of a winner | Manual exit in profit < 0.5R while the target was ≥ 1.5R away | −3 each |
| No-plan trading | n/a, because a session can't start without a plan | 0 |

Bonuses (the score is still capped at 100):

| Behaviour | Bonus |
|---|---|
| Ended the session at TARGET lock | +5 |
| Took ≤ half of `max_trades` and finished green | +5 |

The breakdown is stored per session (`score_breakdown`), with the event ids behind each line so
the report can show *which* trade cost points. Property tests (hypothesis): the score is always
0–100, adding a penalty event never raises it, and the same events always give the same score.

The live score in the HUD is the same function run on events so far.

---

## 7. Report card (`domain/skill/report.py`, pure)

- Headline: **score** (big), grade (A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40, else F), then net ₹ P&L.
- Equity curve over the session, with lock and limit lines.
- Chart of the session with every trade marked.
- Stats: trades, win rate, average win R, average loss R, expectancy R, profit factor, max
  drawdown ₹, fees paid, time in market.
- **Score breakdown** table, each line linking to the trade on the chart.
- **"Biggest mistake"**: the single event with the largest penalty, in one plain sentence
  ("You moved your stop from 67,120 to 66,800 on trade 3, then lost 1.8R").
- Run context: fund before and after, distance to the blown line.

---

## 8. Milestones (each one merges green on its own)

| # | Scope | Done when |
|---|---|---|
| ST1 | Binance adapter (live feed, history, kline cache, 1s/1m), `InstrumentKind.CRYPTO`, `qty_step`, fixtures | Fixture-driven tests for mapping, reconnect, 451 error, microsecond timestamps; a manual script prints live BTC ticks from the VM |
| ST2 | `PaperBroker` shorts, buy-stops, fees, fractional steps | New tests pass **and** all existing paper and live tests stay green |
| ST3 | `skill_*` tables + migration, `domain/skill/rules.py` + `run.py`, `SkillService`, isolation contracts and tests (§4.4) | Isolation tests pass; import-linter passes with the new contracts |
| ST4 | Live arena: sessions, plan confirm ×2, orders, monitor, lock, blown, API, WS | e2e: start session → enter long with stop → hit target → session locks → report available |
| ST5 | Replay arena: day list, `BinanceReplay`, speeds, pause, restart-safe cursor, no-future-leak test | e2e: replay 2024-08-05 BTC at 60x, a stop and a target inside one candle fill the stop first |
| ST6 | Score + report card (pure, property-tested) and the report API | Hypothesis tests pass; known event fixtures give known scores |
| ST7 | Frontend: theme tokens, lobby, setup, arena HUD, ticket with BUY/SELL, draggable lines, markers, lock and blown screens, report, history | Vitest for HUD maths and the ticket; Playwright e2e of the full flow in both themes and at phone width (390 px) |

Run the CI checks before every push: `uv run ruff check`, `ruff format --check`, `mypy`,
`lint-imports`, `alembic upgrade head` + `alembic check`, `pytest` (backend); `npm run typecheck`,
`npm run lint`, `npm test` (frontend).

---

## 9. Phase 2 (design for it, don't build it)

- **Challenges:** preset sessions with fixed plans and goals ("₹2,000 in ≤ 3 trades, max loss
  ₹1,000", "Replay 2024-08-05 and finish green"). Store them as data (`skill_challenges`) so a
  challenge is a plan plus a replay day plus a pass rule.
- **Streaks and badges:** derived from `skill_sessions` (for example 5 sessions in a row scoring
  ≥ 80, "Stopped at target ×10", "Zero revenge trades this week").
- **AI coach:** the `agent/` service reads the report card and events and writes 3 short
  coaching notes. It only reads. No orders.
- **Ready-for-live gate:** an optional setting so live entries need an average Skill Test score
  ≥ X over the last 10 sessions.
- **Leaderboard by score:** only once there are other users. P&L is never ranked.
- **NIFTY/SENSEX replay** from the Kotak recorder (`ReplayMarket`) as a second replay source,
  once real days are recorded. That data is for Aman's personal use only (NSE redistribution
  rules).

## 10. Out of scope

Real crypto trading, any Binance account or API key, deposits, and copying Skill Test trades to
live.
