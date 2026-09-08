"""The recorder's safe evidence timeline and its difficult control path."""
from __future__ import annotations

import json

from smartops.recordings.observation import ObservationBus, safe_state


def test_observation_bus_keeps_monotonic_order_and_only_safe_structure(tmp_path) -> None:
    bus = ObservationBus(tmp_path, "rec-1")
    assert bus.emit("recording_started", source="worker") == 1
    assert bus.emit("action_captured", source="recorder", data={"action": "click"}) == 2

    events = [json.loads(line) for line in (tmp_path / "observations" / "timeline.jsonl").read_text().splitlines()]
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["timestamp_monotonic_ns"] <= events[1]["timestamp_monotonic_ns"]
    assert safe_state({"visible": True, "checked": False, "text": "private", "value": "never"}) == {
        "visible": True, "checked": False
    }
    assert safe_state({"tag": "button", "role": "button", "role_text": "private"}) == {
        "tag": "button", "role": "button"
    }
    assert safe_state({"tag": "private", "role": "classified"}) == {}

    bus.emit("action_captured", source="recorder", data={
        "action": "click", "page": "main", "locator": '[value="private"]',
        "frame": "https://private.example", "file_name": "private.xlsx",
        "before": {"visible": True, "value": "private"},
    })
    last = json.loads((tmp_path / "observations" / "timeline.jsonl").read_text().splitlines()[-1])
    assert last["data"] == {"action": "click", "page": "main", "before": {"visible": True}}


def test_observation_failure_does_not_block_a_recorded_step(tmp_path) -> None:
    from smartops.recordings.worker import PlaywrightRecordingWorker

    captured: list[dict] = []
    worker = PlaywrightRecordingWorker(
        "rec-1", tmp_path, "https://example.test", captured.append,
        lambda: None, lambda _error: None,
    )

    class BrokenBus:
        def emit(self, *args, **kwargs):
            raise OSError("disk full")

    class Page:
        def title(self):
            return ""

    worker._observations = BrokenBus()
    worker._shoot = lambda _page: ""  # type: ignore[method-assign]
    worker._target_state = lambda *_args: {}  # type: ignore[method-assign]
    worker._finish_step((
        {"action": "click", "locator": {"value": '[id="run"]'}},
        Page(), None, "https://example.test/report", "",
    ))

    assert len(captured) == 1
    assert captured[0]["action"] == "click"
    assert worker._observation_error_type == "OSError"


def test_recording_tab_identity_does_not_change_when_an_earlier_tab_closes(tmp_path) -> None:
    from smartops.recordings.worker import PlaywrightRecordingWorker

    class Page:
        def __init__(self) -> None:
            self.context = object()
            self._events: dict[str, object] = {}

        def on(self, name, callback) -> None:
            self._events[name] = callback

    worker = PlaywrightRecordingWorker(
        "rec-1", tmp_path, "https://example.test", lambda _step: None,
        lambda: None, lambda _error: None,
    )
    main, first, second = Page(), Page(), Page()
    worker.primary_page = main
    worker._prevent_debugger_pauses = lambda _page: None  # type: ignore[method-assign]
    worker._bind_downloads = lambda _page: None  # type: ignore[method-assign]
    worker._track_page(main)
    worker._track_page(first)
    worker._track_page(second)
    assert (worker._page_name(first), worker._page_name(second)) == ("page-1", "page-2")
    first._events["close"]()
    assert worker._page_name(second) == "page-2"


def test_pointer_replay_uses_the_current_locator_box_not_a_saved_screen_point(tmp_path) -> None:
    from smartops.adapters.browser.replay import ReplaySession

    class Mouse:
        def __init__(self) -> None:
            self.events: list[tuple] = []

        def move(self, x, y) -> None:
            self.events.append(("move", x, y))

        def down(self) -> None:
            self.events.append(("down",))

        def up(self) -> None:
            self.events.append(("up",))

    class Page:
        def __init__(self) -> None:
            self.mouse = Mouse()

        def is_closed(self) -> bool:
            return False

    class Locator:
        def __init__(self) -> None:
            self.trials: list[tuple[bool, int]] = []

        def click(self, *, trial: bool, timeout: int) -> None:
            self.trials.append((trial, timeout))

        def bounding_box(self):
            return {"x": 40, "y": 60, "width": 100, "height": 80}

    page = Page()
    locator = Locator()
    context = type("Context", (), {"pages": []})()
    session = ReplaySession(context, artifact_dir=tmp_path)
    session._pages = [page]
    session._main_page = page
    session._current = page
    session._page_names = {id(page): "main"}
    session._page_by_name = {"main": page}

    session._do_pointer_click(
        {
            "seq": 1,
            "target": {"page": "main"},
            "locator": {"element_x_ratio": 0.25, "element_y_ratio": 0.75},
        },
        locator,
    )

    assert locator.trials == [(True, session.timeout)]
    assert page.mouse.events == [("move", 65.0, 120.0), ("down",), ("up",)]
