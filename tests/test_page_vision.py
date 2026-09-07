"""What the recorder can see, and whether it admits when it cannot.

A screenshot that came back as a flat grey rectangle used to be indistinguishable
from a good one: the file existed, the step referenced it, and review showed a
grey square nobody could act on. These tests hold the opposite line — a frame is
measured, a blank one is reported as blank, and a route that still returns real
pixels is preferred automatically.
"""

from __future__ import annotations

import os
import struct
import zlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from smartops.recordings.vision import (
    Capture,
    PageVision,
    analyse_frame,
    decode_png,
    observe_page,
)
from tests.recorded_site import LocalSite


def _chromium_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


# ---------- synthetic frames: the measurement itself ----------


def _png(width: int, height: int, pixel, *, filter_type: int = 0) -> bytes:
    """A minimal 8-bit RGB PNG whose colour at (x, y) is ``pixel(x, y)``.

    Written here rather than produced by an imaging library so the decoder is
    tested against bytes this repository controls, including the filter types
    Chrome actually emits.
    """
    raw = bytearray()
    previous = bytearray(width * 3)
    for y in range(height):
        line = bytearray()
        for x in range(width):
            line += bytes(pixel(x, y))
        raw.append(filter_type)
        if filter_type == 0:
            raw += line
        elif filter_type == 2:  # Up
            raw += bytes((line[i] - previous[i]) & 0xFF for i in range(len(line)))
        else:  # Sub
            raw += bytes(
                (line[i] - (line[i - 3] if i >= 3 else 0)) & 0xFF for i in range(len(line))
            )
        previous = line

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def test_a_flat_frame_is_reported_as_blank() -> None:
    quality = analyse_frame(_png(40, 30, lambda x, y: (128, 128, 128)))

    assert quality.decoded
    assert quality.blank
    assert quality.uniform_ratio == 1.0
    assert quality.dominant_color == "#808080"
    assert len(quality.blank_regions) == quality.region_count


def test_a_detailed_frame_is_not_reported_as_blank() -> None:
    quality = analyse_frame(_png(40, 30, lambda x, y: (x * 6 % 256, y * 8 % 256, 90)))

    assert quality.decoded
    assert not quality.blank
    assert not quality.partially_blank
    assert quality.distinct_colors > 50


def test_one_flat_region_is_located_without_calling_the_whole_frame_blank() -> None:
    """The grey block over a working page: normal picture, dead panel."""
    def pixel(x: int, y: int):
        return (128, 128, 128) if y >= 15 else (x * 6 % 256, y * 9 % 256, 40)

    quality = analyse_frame(_png(40, 30, pixel))

    assert not quality.blank
    assert quality.partially_blank
    # A 4x4 grid over the lower half: rows three and four are the covered ones.
    assert set(quality.blank_regions) == {f"r{row}c{col}" for row in (3, 4) for col in range(1, 5)}


@pytest.mark.parametrize("filter_type", [0, 1, 2])
def test_every_filter_chrome_emits_decodes_back_to_the_same_pixels(filter_type: int) -> None:
    data = _png(12, 9, lambda x, y: (x * 20 % 256, y * 25 % 256, 7), filter_type=filter_type)

    decoded = decode_png(data)

    assert decoded is not None
    width, height, channels, pixels = decoded
    assert (width, height, channels) == (12, 9, 3)
    assert pixels[0:3] == bytes((0, 0, 7))
    assert pixels[3:6] == bytes((20, 0, 7))


def test_a_frame_that_cannot_be_read_is_unproven_rather_than_passing() -> None:
    quality = analyse_frame(b"not a png at all")

    assert not quality.decoded
    # Crucially not "blank": an unreadable probe must never masquerade as a verdict.
    assert not quality.blank


# ---------- a real browser: the capture ladder and the observation ----------


@pytest.fixture
def site():
    with LocalSite() as running:
        yield running


@contextmanager
def _page(url: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=_chromium_path())
        try:
            context = browser.new_context(viewport={"width": 900, "height": 600})
            page = context.new_page()
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def test_a_real_page_is_captured_and_trusted(tmp_path, site) -> None:
    vision = PageVision(tmp_path)
    with _page(f"{site.base_url}/plain_form.html") as page:
        capture = vision.capture(page, deep=True)

    assert capture.ok and capture.trustworthy
    assert capture.method in {"surface", "renderer"}
    assert (tmp_path / capture.relative_path).stat().st_size > 0
    assert capture.quality is not None and not capture.quality.blank


def test_a_page_with_nothing_on_it_is_saved_but_not_trusted(tmp_path, site) -> None:
    vision = PageVision(tmp_path)
    with _page(f"{site.base_url}/withheld_frame.html") as page:
        capture = vision.capture(page, deep=True)

    # The frame is still written: evidence that a screen showed nothing is
    # itself worth keeping. What must not happen is calling it good.
    assert capture.ok
    assert not capture.trustworthy
    assert capture.quality is not None and capture.quality.blank
    assert vision.blank_frames == 1


def test_a_covered_panel_is_located_on_a_real_page(tmp_path, site) -> None:
    vision = PageVision(tmp_path)
    with _page(f"{site.base_url}/partly_withheld.html") as page:
        capture = vision.capture(page, deep=True)

    assert capture.trustworthy  # the window itself is fine
    assert capture.quality is not None
    assert capture.quality.partially_blank
    assert capture.quality.blank_regions  # and it says which part is dead


def test_the_observation_reports_controls_without_ever_reporting_a_secret(site) -> None:
    with _page(f"{site.base_url}/plain_form.html") as page:
        observation = observe_page(page)

    identifiers = {control["id"] for control in observation.controls}
    assert {"reference", "site", "agree", "btnRun", "lnkHelp"} <= identifiers
    assert "passwordInput" not in identifiers  # dropped whole, not emptied

    serialised = str(observation.to_dict())
    assert "never-reported" not in serialised  # no field value, secret or not
    assert "A-1024" not in serialised

    by_id = {control["id"]: control for control in observation.controls}
    assert by_id["reference"]["filled"] is True  # "something is in it", not what
    assert by_id["agree"]["checked"] is True
    assert by_id["btnRun"]["disabled"] is True
    assert observation.dialogs and "report is ready" in observation.dialogs[0]["text"]
    assert not observation.drawn_screen


def test_a_drawn_screen_says_so_instead_of_looking_empty(site) -> None:
    with _page(f"{site.base_url}/drawn_screen.html") as page:
        observation = observe_page(page)

    assert observation.drawn_screen
    assert observation.drawn_surfaces[0]["tag"] == "canvas"
    assert observation.controls == []


def test_the_diagnosis_names_a_route_that_can_see_the_page(tmp_path, site) -> None:
    vision = PageVision(tmp_path)
    with _page(f"{site.base_url}/plain_form.html") as page:
        report = vision.diagnose(page)

    assert report["protocol_available"]
    assert report["working_route"] in {"surface", "renderer"}
    assert {route["method"] for route in report["routes"]} == {"surface", "renderer"}
    assert report["verdict"]
    assert report["observation"]["control_count"] > 0


def test_the_diagnosis_reports_a_page_no_route_can_see(tmp_path, site) -> None:
    vision = PageVision(tmp_path)
    with _page(f"{site.base_url}/withheld_frame.html") as page:
        report = vision.diagnose(page)

    assert report["working_route"] == ""
    assert all(route["quality"]["blank"] for route in report["routes"] if route["available"])
    assert "blank" in report["verdict"]


# ---------- a browser without the protocol ----------


class _PageWithoutProtocol:
    """A driver that exposes screenshots but no DevTools session."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 800, "height": 600}
        self.context = self
        self.saved: list[str] = []

    def new_cdp_session(self, page):  # noqa: ARG002 - matches Playwright's shape
        raise RuntimeError("this build has no DevTools protocol")

    def screenshot(self, *, path: str, timeout: int) -> None:  # noqa: ARG002
        Path(path).write_bytes(b"png-bytes")
        self.saved.append(path)


def test_a_browser_without_the_protocol_still_gets_its_screenshot(tmp_path) -> None:
    vision = PageVision(tmp_path)
    page = _PageWithoutProtocol()

    capture = vision.capture(page)

    assert capture.ok and capture.method == "page"
    assert capture.quality is None  # unmeasured, and it does not pretend otherwise
    assert page.saved and Path(page.saved[0]).exists()


def test_a_capture_that_fails_entirely_is_empty_rather_than_raising(tmp_path) -> None:
    class _Broken(_PageWithoutProtocol):
        def screenshot(self, *, path: str, timeout: int) -> None:  # noqa: ARG002
            raise RuntimeError("the tab is gone")

    capture = PageVision(tmp_path).capture(_Broken())

    assert capture == Capture(error="the tab is gone") or not capture.ok
    assert not capture.ok
