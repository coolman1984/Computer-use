"""S-05 tests: live event streaming over WebSocket (/ws/events)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from smartops.api.ws import create_ws_router
from smartops.domain.enums import EventType


def _client(services) -> TestClient:
    app = FastAPI()
    app.include_router(create_ws_router(lambda: services))
    return TestClient(app)


def test_ws_receives_at_least_one_event(services) -> None:
    client = _client(services)
    with client.websocket_connect("/ws/events") as ws:
        run = services.runner.create_run("platform.selfcheck")
        services.runner.execute(run.id)

        message = ws.receive_json()

    assert message["run_id"] == run.id
    assert message["type"] in {e.value for e in EventType}


def test_ws_streams_multiple_events_in_order(services) -> None:
    client = _client(services)
    with client.websocket_connect("/ws/events") as ws:
        run = services.runner.create_run("platform.selfcheck")
        services.runner.execute(run.id)

        received = [ws.receive_json() for _ in range(4)]

    assert [m["type"] for m in received] == [
        "run_created",
        "run_started",
        "step_started",
        "step_succeeded",
    ]
    assert all(m["run_id"] == run.id for m in received)


def test_ws_filters_by_run_id(services) -> None:
    client = _client(services)
    run_a = services.runner.create_run("platform.selfcheck")

    with client.websocket_connect(f"/ws/events?run_id={run_a.id}") as ws:
        run_b = services.runner.create_run("platform.selfcheck")
        services.runner.execute(run_b.id)  # must be completely ignored
        services.runner.execute(run_a.id)  # this is the one that should arrive

        message = ws.receive_json()

    assert message["run_id"] == run_a.id


def test_ws_unsubscribes_on_disconnect(services) -> None:
    assert len(services.bus._subscribers) == 0  # a clean slate before any connection

    client = _client(services)
    with client.websocket_connect("/ws/events") as ws:
        assert len(services.bus._subscribers) == 1
        run = services.runner.create_run("platform.selfcheck")
        services.runner.execute(run.id)
        ws.receive_json()

    assert len(services.bus._subscribers) == 0
