"""Focused safety rules for replay target resolution and pointer gestures."""
from __future__ import annotations

import math

import pytest

from smartops.adapters.browser.replay import ReplaySession, StepFailed


def _session(tmp_path, scope):
    page = type("Page", (), {"is_closed": lambda self: False})()
    session = ReplaySession(type("Context", (), {"pages": []})(), artifact_dir=tmp_path)
    session._current = page
    session._main_page = page
    session._pages = [page]
    session._page_names = {id(page): "main"}
    session._page_by_name = {"main": page}
    session._scope = lambda _action: scope  # type: ignore[method-assign]
    return session


class Locator:
    def __init__(self, count, *, raises_on_wait=False, children=None):
        self._count = count
        self.raises_on_wait = raises_on_wait
        self.wait_timeouts = []
        self.children = children or {}

    def count(self):
        return self._count

    def wait_for(self, *, state, timeout):
        self.wait_timeouts.append(timeout)
        if self.raises_on_wait:
            raise RuntimeError("not visible")

    def locator(self, selector):
        return self.children[selector]


class Scope:
    def __init__(self, locators):
        self.locators = locators

    def locator(self, selector):
        return self.locators[selector]


def test_ambiguous_primary_locator_uses_a_unique_recorded_fallback(tmp_path) -> None:
    primary, fallback = Locator(2), Locator(1)
    session = _session(tmp_path, Scope({"primary": primary, "fallback": fallback}))

    result = session._locate({
        "seq": 1, "target": {"page": "main"},
        "locator": {"value": "primary", "fallbacks": ["fallback"]},
    })

    assert result is fallback
    assert primary.wait_timeouts == []
    assert fallback.wait_timeouts and all(timeout > 0 for timeout in fallback.wait_timeouts)


def test_ambiguous_locator_never_silently_selects_first_match(tmp_path) -> None:
    session = _session(tmp_path, Scope({"duplicate": Locator(2)}))

    with pytest.raises(StepFailed, match=r"ambiguous \(2 matching elements\)"):
        session._locate({
            "seq": 7, "target": {"page": "main"},
            "locator": {"value": "duplicate"},
        })


def test_anchor_resolves_a_unique_control_inside_a_unique_nearby_container(tmp_path) -> None:
    target = Locator(1)
    container = Locator(1, children={"button[type=submit]": target})
    session = _session(tmp_path, Scope({"[data-testid=report-filters]": container}))

    result = session._locate({
        "seq": 8,
        "target": {"page": "main"},
        "locator": {
            "anchor": {
                "container": "[data-testid=report-filters]",
                "target": "button[type=submit]",
            }
        },
    })

    assert result is target
    assert container.wait_timeouts and target.wait_timeouts


def test_anchor_rejects_an_ambiguous_control_instead_of_selecting_first(tmp_path) -> None:
    container = Locator(1, children={"button": Locator(2)})
    session = _session(tmp_path, Scope({"[data-testid=report-filters]": container}))

    with pytest.raises(StepFailed, match="target inside its recorded anchor is not unique"):
        session._locate({
            "seq": 9,
            "target": {"page": "main"},
            "locator": {
                "anchor": {"container": "[data-testid=report-filters]", "target": "button"}
            },
        })


def test_anchor_rejects_ambiguous_container_instead_of_using_a_nearby_match(tmp_path) -> None:
    session = _session(tmp_path, Scope({"[data-testid=report-filters]": Locator(2)}))

    with pytest.raises(StepFailed, match="recorded anchor is not unique"):
        session._locate({
            "seq": 10,
            "target": {"page": "main"},
            "locator": {
                "anchor": {"container": "[data-testid=report-filters]", "target": "button"}
            },
        })


def test_semantic_fallback_requires_one_matching_live_control(tmp_path) -> None:
    semantic = Locator(1)
    session = _session(tmp_path, Scope({'button[type="button"]': semantic}))

    result = session._locate({
        "seq": 11,
        "target": {"page": "main"},
        "locator": {"semantic": {"tag": "button", "role": "button", "type": "button"}},
    })

    assert result is semantic


def test_pointer_rejects_non_finite_saved_ratios(tmp_path) -> None:
    class Mouse:
        def move(self, *_args):
            raise AssertionError("must not move")

    page = type("Page", (), {"is_closed": lambda self: False, "mouse": Mouse()})()
    locator = type("Locator", (), {
        "click": lambda self, **_kwargs: None,
        "bounding_box": lambda self: {"x": 0, "y": 0, "width": 10, "height": 10},
    })()
    session = ReplaySession(type("Context", (), {"pages": []})(), artifact_dir=tmp_path)
    session._current = page
    session._main_page = page
    session._pages = [page]
    session._page_names = {id(page): "main"}
    session._page_by_name = {"main": page}

    for invalid in (math.nan, math.inf, -math.inf, "not-a-number"):
        with pytest.raises(StepFailed, match="pointer x position is invalid"):
            session._do_pointer_click(
                {"seq": 1, "target": {"page": "main"}, "locator": {"element_x_ratio": invalid}},
                locator,
            )


def test_pointer_release_failure_is_unknown_effect_and_is_not_retried(tmp_path) -> None:
    class Mouse:
        def __init__(self):
            self.calls = []

        def move(self, *_args):
            self.calls.append("move")

        def down(self):
            self.calls.append("down")

        def up(self):
            self.calls.append("up")
            raise RuntimeError("transport lost")

    mouse = Mouse()
    page = type("Page", (), {"is_closed": lambda self: False, "mouse": mouse})()
    locator = type("Locator", (), {
        "click": lambda self, **_kwargs: None,
        "bounding_box": lambda self: {"x": 0, "y": 0, "width": 10, "height": 10},
    })()
    session = ReplaySession(type("Context", (), {"pages": []})(), artifact_dir=tmp_path)
    session._current = page
    session._main_page = page
    session._pages = [page]
    session._page_names = {id(page): "main"}
    session._page_by_name = {"main": page}

    with pytest.raises(StepFailed, match="remote effect is unknown"):
        session._do_pointer_click(
            {"seq": 2, "target": {"page": "main"}, "locator": {}}, locator,
        )
    assert mouse.calls == ["move", "down", "up", "up"]
