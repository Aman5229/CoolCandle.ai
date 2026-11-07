# 0004: The buddy runs in its own service

**Status:** accepted (D15)

The buddy's model loop moved out of core into `agent/`, a separate FastAPI service with its own
project, tests and container. It holds the system prompt, the tool definitions, the conversation
history, the call to Claude and the check that every number in a reply came from a tool.

It has no database, no broker and no Kotak credentials. Everything it knows or does goes through
core:

1. The browser calls core's `POST /api/agent/chat`, which needs a session and the CSRF token.
2. Core makes a random tool token, keeps it in memory for this request only, and calls the agent's
   `POST /chat` with the shared service token. Nobody else can start a chat.
3. The agent calls `POST /internal/agent/tools/{name}` with the tool token. Core runs the tool for
   that trader and mode. When the chat request ends, the token is gone.
4. Core returns the agent's text with the signal card that core's own `propose_trade` approved. The
   agent's words never become numbers on a card.

`/internal` sits outside `/api`, and Caddy forwards only `/api` and `/ws`, so the tool route is
reachable on the private network alone. A test checks the Caddyfile.

Why: the part that talks to a model on the internet now cannot reach the broker or the database
even if it misbehaves, which makes "one door to the broker" true by construction. The agent can
also be changed, restarted or scaled without touching the trading process.

Costs: one more container, a shared secret to manage, and a network hop per tool call. If the
agent is down, the Buddy page says so and trading carries on.

The contract:

```
core → agent  POST /chat   {"conversation", "trading_date", "message", "tool_token"} → {"text"}
agent → core  POST /internal/agent/tools/{name}   JSON input → JSON result, or {"code", "message"}
```

Both sides test against the same example request.
