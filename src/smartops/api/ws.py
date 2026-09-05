"""بث حي للأحداث عبر WebSocket: /ws/events[?run_id=...] (انظر events/bus.py).

المسار يشترك في services.bus وقت الاتصال وينقل كل حدث جديد فورًا للعميل،
ويلغي الاشتراك تلقائيًا عند قطع الاتصال حتى لا يتسرّب مستمع بلا فائدة.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..domain.models import Event
from ..services import Services


def create_ws_router(get_services: Callable[[], Services]) -> APIRouter:
    """يبني راوتر /ws/events مربوطًا بمزوّد خدمات معيّن.

    مزوّد منفصل (بدل استيراد api.app.get_services مباشرة) لتفادي استيراد
    دائري بين app.py وws.py، ولتسهيل الاختبار بخدمات معزولة.
    """
    router = APIRouter()

    @router.websocket("/ws/events")
    async def stream_events(websocket: WebSocket) -> None:
        run_id = websocket.query_params.get("run_id")
        await websocket.accept()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()

        def on_event(event: Event) -> None:
            # ينادى من أي خيط متزامن (المحرك/العامل)؛ ننقله بأمان لحلقة asyncio.
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
