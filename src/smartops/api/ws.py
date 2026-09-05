"""Live event streaming over WebSocket: /ws/events[?run_id=...] (see events/bus.py).

The route subscribes to services.bus on connect and forwards every new event
to the client immediately, unsubscribing automatically on disconnect so no
useless listener leaks.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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

    return router
