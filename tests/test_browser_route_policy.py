"""Route comparison must preserve an already-correct application view."""
from __future__ import annotations

from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter


def test_route_policy_ignores_extra_transient_query_noise() -> None:
    assert not PlaywrightBrowserAdapter._needs_entry_navigation(
        "https://portal.example/report?period=2026-09&trace=temporary",
        "https://portal.example/report?period=2026-09",
        {"transient_query_params": ["trace"]},
    )


def test_route_policy_preserves_declared_business_query_parameters() -> None:
    assert PlaywrightBrowserAdapter._needs_entry_navigation(
        "https://portal.example/report?period=2026-08",
        "https://portal.example/report?period=2026-09",
        {},
    )


def test_route_policy_can_mark_a_query_absent_from_the_start_url_as_business() -> None:
    assert PlaywrightBrowserAdapter._needs_entry_navigation(
        "https://portal.example/report?organisation=VD",
        "https://portal.example/report",
        {"business_query_params": ["organisation"]},
    )


def test_route_policy_treats_hash_as_a_view_only_when_configured() -> None:
    current = "https://portal.example/report#summary"
    required = "https://portal.example/report#detail"
    assert not PlaywrightBrowserAdapter._needs_entry_navigation(current, required, {})
    assert PlaywrightBrowserAdapter._needs_entry_navigation(
        current, required, {"hash_is_business_view": True}
    )
