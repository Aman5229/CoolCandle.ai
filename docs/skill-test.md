# Skill Test

> A practice arena inside CoolCandle.ai: past NIFTY and SENSEX days replayed minute by minute,
> index options priced by a model, a practice fund in rupees, the same discipline rules as the
> live day, and a score for how well you kept them. Specified 7 Nov 2026, built 8 Nov
> 2026. Read `BLUEPRINT.md` §0 first: Decimal money, UTC datetimes, the
> import-linter layers and tests before merge all apply here.

---

## 0. Read this first

1. **Skill Test can never touch live trading.** No Skill Test code path reaches a Kotak broker,
   the live `BrokerRouter`, the live `daily_plans`, `day_locks` or `trades` rows, or the live day
   P&L. This is the one rule that must not break. §4.4 lists the tests that enforce it.
2. **It is not PAPER mode.** PAPER (the header switch) rehearses today with Kotak's live prices.
   Skill Test is a separate world: past days, its own fund that lasts across sessions, its own
   tables and screens.
3. **Reuse, don't fork.** Fills come from `PaperBroker`, charges from `domain/costs.py` (the live
   rate card), the chart from `PriceChart.tsx`. Where a live rule doesn't fit, Skill Test has a
   small rule module of its own (`domain/skill/rules.py`); the live guard is untouched.
4. **Discipline first, P&L second.** Every screen ranks the discipline score above profit.
5. Items marked **DEFAULT** in §2 are not confirmed yet. They are built to be easy to change;
   record Aman's answer in §2.

---

## 1. What the user gets

- **A run:** a practice fund (₹50,000 / ₹1,00,000 / ₹5,00,000) and a day to call **today**, for
  example 8 Nov 2025. The chart shows the year before it; the days after it are the future.
- **Sessions:** each one plays the run's next trading day from 09:15, at 1x / 5x / 20x / 60x
  with pause. When the session ends, today moves on to the next trading day. A played day never
  comes back, so no session can peek at the future.
- **A day plan,** as live: profit target, loss limit, max trades, max loss a trade, cool-off,
  reward to risk, entry window and square-off. Confirmed twice (type `START`), then stricter
  only. A limit or the square-off locks the day.
- **Options:** buy CE or PE on the session's index and weekly expiry. Premiums come from a
  model, because expired contracts have no history.
- **Charts** in 1m, 5m, 15m, 1h and 1D, opening on readable candles, never past the replay.
- **Discipline score (0 to 100)** live in the HUD, and a **report card** after every session.
- **Blown run:** if the fund falls 30% below its start, the run ends and stays in history.
- **Its own look:** an arcade/HUD theme, so it can never be mistaken for the live screens.

---

## 2. Decisions

| # | Question | Answer | Status |
|---|---|---|---|
| S1 | Data | **Kotak's 1-minute index history**: NIFTY, SENSEX and India VIX, fetched once by Aman with `uv run coolcandle fetch-index-history` (three years by default). It needs only the consumer key: no TOTP, no login, so it never ends a trading session. Stored as Parquet under `data/skill/history/`. | **CONFIRMED** (Aman, 8 Nov 2026) |
| S2 | What is traded | **Index options, priced by a model** (§3.2). | **CONFIRMED** (Aman, 8 Nov 2026) |
| S3 | Timeline | **A run has its own today**; each session is the next trading day, and today only moves forward. | **CONFIRMED** (Aman, 8 Nov 2026) |
| S4 | Wording on screen | No ribbon and no "fake money" wording: the theme and the HUD mark it as practice. | **CONFIRMED** (Aman, 8 Nov 2026) |
| S5 | Open while the live day is locked? | **Closed** by default: new sessions and entries are refused with `LIVE_LOCKED`, and a running session is not locked. `COOLCANDLE_SKILL_WHEN_LIVE_LOCKED=true` keeps it open with a banner. | DEFAULT |
| S6 | Trades at once | **One** open trade a session, as live. Buy only, as live. | DEFAULT |
| S7 | Blown threshold | The run ends at **−30%** of its starting fund. | DEFAULT |
| S8 | Session plan edits | Confirmed twice, then **stricter only**, as live. | DEFAULT |
| S9 | The model | Black-Scholes on the replayed index. Volatility: **India VIX** at that minute, or the index's own 20-day realised volatility when Kotak has no VIX for the day. Risk-free rate 6.5%. Premiums on a ₹0.05 grid; bid and ask 0.5% apart, at least a tick each side; a market buy fills at the ask plus a tick. Lot sizes today's (NIFTY 65, SENSEX 20) for every date. Charges from the live rate card. | DEFAULT |

---

## 3. Data and the model

### 3.1 History

- `uv run coolcandle fetch-index-history [--years 3]`, from `backend/`. It reads the first saved
  Kotak credentials, uses only the consumer key, and calls `historical_data` for
  `nse_cm|Nifty 50`, `bse_cm|SENSEX` and India VIX. Kotak serves 30 days of minutes a request and
  rate-limits hard, so it pauses 2 seconds between requests and backs off on HTTP 429: about five
  minutes for three years. Running it again tops the files up.
- Files: `data/skill/history/<INDEX>/1m/<YYYY-MM>.parquet`, prices as whole hundredths. Longer
  timeframes are folded from the minutes inside pyarrow (a year of minutes as Python objects
  would be over 100 MB), with bars starting at 09:15, the same rule as the live chart.
- Known gaps (from the previous project): index volume is always 0, and Kotak is missing
  15:16–15:28 on some recent days. Square-off at 15:15 makes the second harmless.
- How far back Kotak's minutes go is reported by the command; it was verified for one year only.

### 3.2 Options

- Weekly expiry by the exchange's weekday for that date: NIFTY Thursday, then Tuesday from
  1 Sep 2025; SENSEX Friday, then Tuesday from January 2025 (the 3 Jan contract kept its
  Friday), then Thursday from 1 Sep 2025. An expiry on a holiday moves to the trading day before.
- Strikes: NIFTY every 50, SENSEX every 100; the chain shows ten either side of the money, and
  any strike on the grid can be traded.
- **The limit, stated plainly:** model premiums are not real ones. A model can't reproduce
  volatility skew or expiry-day behaviour. Skill Test is for practising discipline, not for
  testing a strategy.

### 3.3 Replay

- One session plays one day's minutes through its own clock. Each minute is walked open, high,
  low, close for a rising minute (open, low, high, close for a falling one), with evenly spaced
  prices between, so the high and low are always reached exactly.
- **If an open trade's stop and target both fall inside one minute, the stop fills first.** The
  arena prices the option at the minute's low and high; when both are reached, the replay walks
  the stop's side first.
- On restart a session comes back **paused** where it was (the cursor is saved every 5 seconds).

---

## 4. Backend

### 4.1 Layout

```
domain/skill/      models.py rules.py options.py charts.py score.py report.py run.py   (pure)
ports/skill.py     IndexData, Replay, ReplayFactory, SkillBrokerFactory
adapters/market/history/   store.py (Parquet), replay.py (CandleReplay)
adapters/brokers/kotak_neo/history.py   Kotak's history, shared with the live feed
services/skill/    service.py (SkillService, the one way in), arena.py (one session), store.py, views.py
api/routes/skill.py    /api/skill
cli.py             fetch-index-history
```

Import-linter: `services.skill` never imports the live orders, guard, reconcile or trailing
services or the Kotak adapter, and no live service imports Skill Test. The arena is Skill Test's
one door to a broker, and only to the PaperBroker built for that session;
`tests/test_architecture.py` checks both doors.

### 4.2 Tables (all `skill_`)

| Table | Key columns |
|---|---|
| `skill_accounts` | user_id (unique), settings |
| `skill_runs` | account_id, number, start_fund, cash, status (ACTIVE/BLOWN/RETIRED), **today**, started_at, ended_at, peak_equity |
| `skill_sessions` | run_id, index, day, replay_cursor, speed, plan, plan_status, state, lock_reason, started_at, ended_at, start_equity, end_equity, score, score_breakdown |
| `skill_trades` | session_id, instrument_key, trading_symbol, status, qty, entry, stop_at_fill, stop, target, exit, r_risk, pnl, fees, opened_at, closed_at, exit_kind, stop_widened |
| `skill_orders` | session_id, trade_id, role, broker_order_id, request, status, filled_qty, avg_price, ts |
| `skill_events` | session_id, ts, kind, trade_id, payload: the behaviour log the score is computed from |

### 4.3 Rules (`domain/skill/rules.py`)

Before every entry: the session is active (not a draft, cooling off, outside the entry window,
locked or over) and the run is active; the replay is playing; no open trade; trades left; at
least one lot; a stop below the entry and at least 0.5% away; worst-case loss (stop a tick worse,
both sides' charges) within the per-trade limit and what is left of the loss limit; reward to
risk above the plan's when a target is set; and the funds to pay for the option. A refused
attempt is logged as `BLOCKED` and costs points. Locks: TARGET, LOSS_LIMIT, MAX_TRADES,
SQUARE_OFF, and BLOWN when the fund falls through its floor; an open trade is sold at market.

### 4.4 Isolation tests (must pass)

- A whole Skill Test session leaves `daily_plans`, `plan_changes`, `day_locks`, `trades`,
  `orders`, `trade_events`, `audit_log` and `signals` untouched.
- A Skill Test lock doesn't change the live or paper day status.
- With a Kotak broker registered as live, it gets zero calls.
- A Skill Test option is unknown to live `OrderService`.
- A live lock closes Skill Test by default (S5), and only shows a banner with the setting on.

### 4.5 API (`/api/skill`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/skill/account` | Run, today, the stored history's range, recent scores |
| POST | `/skill/runs` | `{start_fund, start_day, typed?}`: a new run (`RETIRE` to end the current one) |
| GET | `/skill/runs` · `/skill/runs/{id}/sessions` | History |
| POST | `/skill/sessions` | `{index, speed, plan}`: a draft for the run's today |
| POST | `/skill/sessions/{id}/confirm` | Step 1, then step 2 with `typed: "START"` |
| PATCH | `/skill/sessions/{id}/plan` | Stricter-only changes once started |
| POST | `/skill/sessions/{id}/replay` | `{action: play\|pause\|speed, speed?}` |
| GET | `/skill/sessions/{id}/chain` | Strikes around the money, at model prices |
| POST | `/skill/sessions/{id}/preview` · `/orders` | `{instrument_key, lots, order_type, price?, stop, target?}` |
| PATCH | `/skill/trades/{id}/stop` | Move the stop (widening is allowed, and costs points) |
| POST | `/skill/trades/{id}/exit` | Sell at market |
| POST | `/skill/sessions/{id}/end` | End early: no penalty, and the day is spent |
| GET | `/skill/sessions/{id}` · `/report` | State, trades, live score; the report card |
| GET | `/skill/sessions/{id}/candles?interval=` | 1m, 5m, 15m, 1h, 1d; never past the cursor |

WebSocket: `skill.tick`, `skill.candle`, `skill.session`, `skill.trade`, `skill.locked`,
`skill.run_blown`, `skill.replay`, each sent only to the user it belongs to.

---

## 5. Frontend (`features/skill/`)

| Screen | Contents |
|---|---|
| Lobby | Fund card (today in this run, blown line), start or retire a run with a start day, "Play <day>", last scores; when there is no history, the fetch command |
| Setup | Index, speed, the day plan, then the summary dialog and `START` |
| Arena | HUD (fund, P&L meter, trades left, score, replay clock and time to square-off), index chart with timeframes and trade markers, option chain, ticket, open trade, replay controls, back to lobby |
| Lock and blown | Full-screen: "Target hit 🎯" (confetti), "Loss limit reached", "Max trades used", "Day over", "Account blown" |
| Report card | §8 |
| History | Runs, their sessions, their report cards |

Skill Test's colours come from tokens under `[data-arena="skill"]`, in light and dark, with the
one-second cross-fade. The live header is replaced while it is open (a phone keeps a slim bar
for its menu). Charts open on the latest hundred bars; you can scroll back through the year.

---

## 6. Milestones

Built 8 Nov 2026, with tests at every layer: the option model is property-tested (put-call parity, bounds, monotonic in price and
volatility); the store folds timeframes exactly as the domain does; a replayed day is played tick
by tick through the real services; the browser test plays a seeded NIFTY day at 60x on a laptop
and on a 390px phone. CI never calls Kotak.

## 7. Discipline score (`domain/skill/score.py`, pure)

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

## 8. Report card (`domain/skill/report.py`, pure)

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
- **Real option premiums** from the Kotak recorder, for days it recorded, in place of the model.


## 10. Out of scope

Real trades from Skill Test, copying Skill Test trades to live, and showing the history to anyone
but Aman (NSE data is for personal use).
