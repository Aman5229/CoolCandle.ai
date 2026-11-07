# CoolCandle.ai

An overtrading guard for Indian index options on your own Kotak Neo account. Each morning you lock
a plan: a profit target, a loss limit and a maximum number of trades. Every trade carries a stop at
the exchange. When a limit is reached, open trades exit, pending orders cancel, and ordering stays
locked until the next trading day.

On top of that sit a trailing stop, a decision model that proposes trades from your setups, and a
chat buddy that explains them. Nothing enters a trade without a tap on screen.

`BLUEPRINT.md` is the source of truth for what is built and why.

> This is software, not investment advice. Most retail options traders lose money. A stop is a
> plan, not a promise: in a fast market it can fill worse than its price.

## Run it locally

You need [uv](https://docs.astral.sh/uv/), Node 24 and Docker. The default setup uses simulated
prices and a simulated clock, so it needs no broker and works at any hour.

```bash
# 1. Postgres
docker compose -f deploy/docker-compose.yml up -d postgres

# 2. Backend
cd backend
cp .env.example .env
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    paste that into COOLCANDLE_SECRET_KEY in .env
uv sync
uv run alembic upgrade head
uv run coolcandle create-admin you@example.com
#    add the printed otpauth:// link to your authenticator app
uv run uvicorn coolcandle.main:create_app --factory --port 8000

# 3. Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and sign in. Paper mode is the default at every login.

### The buddy (optional)

The buddy runs as its own service, `agent/`. It has no database and no broker: it asks core for
every fact and action. Without it, the Buddy page says it is not set up and everything else works.

```bash
# a token both services share
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
#    put it in COOLCANDLE_AGENT_TOKEN in backend/.env and agent/.env,
#    and set COOLCANDLE_AGENT_URL=http://localhost:8001 in backend/.env

cd agent
cp .env.example .env    # add COOLCANDLE_AGENT_ANTHROPIC_API_KEY to talk to the real model
uv sync
uv run uvicorn coolcandle_agent.main:create_app --factory --port 8001
```

Restart the backend after changing `backend/.env`.

A lock holds for its whole date, including across restarts. To try a fresh simulated day, set
`COOLCANDLE_SIM_DATE` in `backend/.env` to a later trading day and restart the backend.

## Checks

```bash
cd backend
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
uv run lint-imports

cd ../agent
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy

cd ../frontend
npm run typecheck && npm run lint && npm test && npm run build
npm run e2e   # sign in, lock a plan, paper trade, hit the lock, in a real browser
```

In `agent/`, `uv run pytest --agent-evals` runs the buddy's evals on the real model. It needs
`COOLCANDLE_AGENT_ANTHROPIC_API_KEY` and costs money, so it never runs by default.

## Backtesting

The recorder saves real ticks from the first live day. Once there are enough:

```bash
uv run coolcandle backtest opening_range 2026-11-01 2027-03-01 --daily-loss-limit 5000
```

It tests on days the setup was not tuned on, through the real guard and paper broker with full
charges, and reports against the gate in `BLUEPRINT.md` §16.2.

## Going live

Read `docs/broker-notes/kotak-neo.md` first, especially what has and has not been verified.

- API orders only work from the static IP whitelisted in Kotak's Trade API settings.
- Connecting ends every other session on the account, so never run two instances.
- Set `COOLCANDLE_MARKET_SOURCE=kotak`. Live mode refuses to run on simulated prices.
- Start at one lot.

## Layout

```
backend/src/coolcandle/
  domain/     pure rules: guard, plan, sizing, costs, trade states, events, trailing, setups
  ports/      the interfaces: Broker, MarketData, Clock, SignalSource, Notifier, Agent
  services/   orders (the one door to the broker), guard, market, signals, agent, ...
  adapters/   Kotak Neo, paper, simulator, replay, Telegram, RSS, the agent service client
  api/        REST routes, the WebSocket, and the agent's internal tool routes
  db/         tables and migrations
agent/        the buddy's own service: model, conversation, number check (ADR 0004)
frontend/src/ React app, by feature
deploy/       Docker Compose, Caddy, backups
```

Layer rules are enforced by import-linter. No code outside `OrderService` calls the broker's order
methods, and a test checks that.
