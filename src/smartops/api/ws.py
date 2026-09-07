"""Live event streaming over WebSocket: /ws/events[?run_id=...] (see events/bus.py).

The route subscribes to services.bus on connect and forwards every new event
to the client immediately, unsubscribing automatically on disconnect so no
useless listener leaks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..chrome_bridge import valid_extension_origin
from ..domain.models import Event
from ..services import Services


def create_ws_router(get_services: Callable[[], Services]) -> APIRouter:
    """Build the /ws/events router bound to a specific services provider.

    A separate provider (instead of importing api.app.get_services directly)
    avoids a circular import between app.py and ws.py, and makes testing with
    isolated services easier.
    """
    router = APIRouter()

    @router.websocket("/ws/events")
    async def stream_events(websocket: WebSocket) -> None:
        run_id = websocket.query_params.get("run_id")
        await websocket.accept()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()

        def on_event(event: Event) -> None:
            # Called from any synchronous thread (engine/worker); hand it safely to the asyncio loop.
            loop.call_soon_threadsafe(queue.put_nowait, event)

        svc = get_services()
        unsubscribe = svc.bus.subscribe(on_event)
        try:
            while True:
                event = await queue.get()
                if run_id and event.run_id != run_id:
                    continue
                await websocket.send_json(event.to_dict())
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    @router.websocket("/ws/chrome-bridge")
    async def chrome_bridge(websocket: WebSocket) -> None:
        """Accept safe structure from the one tab explicitly shared in Chrome.

        This endpoint is intentionally extension-only.  It does not perform
        browser actions and rejects oversized or unknown messages before they
        reach the core.
        """
        origin = websocket.headers.get("origin", "")
        if not valid_extension_origin(origin):
            await websocket.close(code=1008, reason="Chrome extension origin required")
            return
        await websocket.accept()
        connection_id = uuid4().hex
        extension_id = origin.removeprefix("chrome-extension://")
        svc = get_services()
        svc.chrome_bridge.connect(connection_id, extension_id)
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 512_000:
                    await websocket.close(code=1009, reason="Snapshot is too large")
                    return
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                    continue
                if not isinstance(message, dict):
                    await websocket.send_json({"type": "error", "message": "Object required"})
                    continue
                message_type = message.get("type")
                try:
                    if message_type in {"hello", "heartbeat"}:
                        svc.chrome_bridge.heartbeat(connection_id)
                    elif message_type == "snapshot":
                        svc.chrome_bridge.update(connection_id, message.get("snapshot"))
                    elif message_type == "unshare":
                        svc.chrome_bridge.unshare(connection_id)
                    elif message_type == "error":
                        svc.chrome_bridge.fail(connection_id, message.get("message"))
                    else:
                        raise ValueError("Unknown message type")
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                await websocket.send_json({"type": "ack", "messageType": message_type})
        except WebSocketDisconnect:
            pass
        finally:
            svc.chrome_bridge.disconnect(connection_id)

    return router
