"""The one WebSocket: server pushes day status, orders, trades, alerts, and subscribed ticks."""

import asyncio
import contextlib
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from coolcandle.api.deps import SESSION_COOKIE

router = APIRouter()
TICK_GAP_S = 0.25  # at most four price updates a second per instrument


@router.websocket("/ws")
async def stream(socket: WebSocket) -> None:
    app = socket.app.state.container
    token = socket.cookies.get(SESSION_COOKIE)
    user = await app.auth.current(token) if token else None
    origin = socket.headers.get("origin")
    if user is None or (origin is not None and origin != app.settings.allowed_origin):
        await socket.close(code=4401)
        return
    await socket.accept()
    queue = app.bus.subscribe()
    wanted: set[str] = {i.key for i in app.instruments.indices()}
    last_sent: dict[str, float] = {}

    async def listen() -> None:
        """The client only ever says which instruments it wants."""
        while True:
            message = await socket.receive_json()
            if message.get("type") == "subscribe":
                wanted.update(str(key) for key in message.get("keys", []))

    listener = asyncio.create_task(listen())
    try:
        while True:
            event = await queue.get()
            kind = event["type"]
            if kind == "tick":
                key = event["data"]["instrument_key"]
                now = time.monotonic()
                if key not in wanted or now - last_sent.get(key, 0.0) < TICK_GAP_S:
                    continue
                last_sent[key] = now
            elif kind == "day_status" and event["data"]["mode"] != user.mode:
                continue
            await socket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
            await listener
        app.bus.unsubscribe(queue)
