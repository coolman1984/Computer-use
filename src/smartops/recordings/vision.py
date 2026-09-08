"""What the agent can actually see of a page, and an honest verdict when it cannot.

A recording is only as good as the evidence attached to each step. Two things
routinely destroy that evidence on corporate portals, and neither of them
announces itself:

* **The picture comes back blank.** A screenshot is normally taken from the
  window's composited surface. Anything that owns that surface can withhold it —
  protected media, a data-loss-prevention agent, an occluded or minimised
  window, a GPU process that has lost its context. Chrome hands back a
  perfectly valid PNG of a flat grey rectangle, `page.screenshot()` reports
  success, and the recording fills with grey frames nobody notices until replay
  is built on them. So a captured frame is *measured* here before it is
  believed, and when it is flat the capture is retried by a route that does not
  read the surface at all (``fromSurface: false`` renders inside the renderer
  process).

* **The screen is drawn, not built.** Nexacro-style applications paint their
  whole UI onto one canvas. There is no button element to find, so a DOM
  snapshot of such a screen is nearly empty and tells the agent nothing. The
  observation below therefore reports drawn surfaces explicitly, so "I can see
  no controls" is distinguishable from "this screen has no controls to see".

Nothing here changes the page. Every routine is read-only evidence, which is
what the platform's action ladder requires of anything vision-shaped.

Chrome DevTools Protocol ``Page.captureScreenshot``:
https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-captureScreenshot
"""

from __future__ import annotations

import base64
import json
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_url

# A frame this uniform is not a picture of a working application. 0.985 rather
# than 1.0 because a real greyed-out frame still carries a cursor, a scrollbar
# edge, or JPEG-ish ringing along one border, and demanding a perfectly single
# colour would let those few pixels vouch for an empty screenshot.
BLANK_UNIFORM_RATIO = 0.985

# The verdict is taken from a deliberately tiny copy of the frame. The decoder
# below is pure Python, so cost scales with pixel count: at this scale a
# 1440x900 viewport becomes about 86x54 — small enough to decode in a few
# milliseconds, large enough that a grey panel covering a quarter of the screen
# still lands in its own tile.
PROBE_SCALE = 0.06

# How the frame is divided when reporting *where* it went flat. A page that is
# entirely blank and a page with one grey block over its content area are two
# different faults with two different fixes, and only a per-region answer tells
# them apart.
PROBE_GRID = 4


@dataclass(frozen=True)
class FrameQuality:
    """The measured verdict on one captured frame."""

    decoded: bool  # False when the probe could not be read; every other field is then unproven
    uniform_ratio: float = 0.0  # share of pixels holding the single most common colour
    dominant_color: str = ""  # "#rrggbb" of that colour
    distinct_colors: int = 0
    blank: bool = False  # the whole frame is one colour
    blank_regions: tuple[str, ...] = ()  # e.g. ("r2c1", "r2c2") — flat tiles of a PROBE_GRID grid
    region_count: int = 0

    @property
    def partially_blank(self) -> bool:
        """A frame that is not wholly flat but is hiding part of itself."""
        return not self.blank and bool(self.blank_regions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decoded": self.decoded,
            "uniform_ratio": round(self.uniform_ratio, 4),
            "dominant_color": self.dominant_color,
            "distinct_colors": self.distinct_colors,
            "blank": self.blank,
            "partially_blank": self.partially_blank,
            "blank_regions": list(self.blank_regions),
            "region_count": self.region_count,
        }


@dataclass(frozen=True)
class Capture:
    """One saved frame and how it had to be obtained."""

    relative_path: str = ""
    method: str = ""  # "surface" | "renderer" | "page" | ""
    quality: FrameQuality | None = None
    escalated: bool = False  # the first route came back flat and a second was used
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.relative_path)

    @property
    def trustworthy(self) -> bool:
        """Whether this frame is worth showing as evidence of a step."""
        return self.ok and not (self.quality and self.quality.blank)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "method": self.method,
            "escalated": self.escalated,
            "trustworthy": self.trustworthy,
            "error": self.error,
            "quality": self.quality.to_dict() if self.quality else None,
        }


# ---------------------------------------------------------------------------
# Reading a PNG without a third-party imaging library
# ---------------------------------------------------------------------------
#
# Adding Pillow to run one uniformity check would put a compiled dependency on
# every operator machine for a job the standard library already does: Chrome
# writes 8-bit, non-interlaced RGB or RGBA PNGs, and their pixel data is a zlib
# stream of filtered scanlines. Decoding only the small probe keeps the pure
# Python cost irrelevant.
#
# PNG specification, filtering: https://www.w3.org/TR/png/#9Filters


def _unfilter_row(kind: int, line: bytearray, previous: bytes, bpp: int) -> None:
    """Reverse one scanline filter in place. Raises ValueError on an unknown filter."""
    if kind == 0:  # None
        return
    width = len(line)
    if kind == 1:  # Sub
        for i in range(bpp, width):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif kind == 2:  # Up
        for i in range(width):
            line[i] = (line[i] + previous[i]) & 0xFF
    elif kind == 3:  # Average
        for i in range(width):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
    elif kind == 4:  # Paeth
        for i in range(width):
            a = line[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            nearest = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[i] = (line[i] + nearest) & 0xFF
    else:
        raise ValueError(f"unsupported PNG filter {kind}")


def decode_png(data: bytes) -> tuple[int, int, int, bytes] | None:
    """(width, height, channels, pixel bytes) for a plain 8-bit PNG, else None.

    None means "this frame cannot be measured", which the callers report as an
    unproven verdict rather than as a passing one.
    """
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    position = 8
    width = height = depth = colour = interlace = 0
    compressed = bytearray()
    while position + 8 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        position += 12 + length  # length + type + body + CRC
        if kind == b"IHDR" and len(body) >= 13:
            width = int.from_bytes(body[0:4], "big")
            height = int.from_bytes(body[4:8], "big")
            depth, colour, interlace = body[8], body[9], body[12]
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
    channels = {2: 3, 6: 4}.get(colour, 0)
    if depth != 8 or interlace != 0 or not channels or width <= 0 or height <= 0:
        return None
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < (stride + 1) * height:
        return None
    pixels = bytearray(stride * height)
    previous = bytes(stride)
    cursor = 0
    for row in range(height):
        kind = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        try:
            _unfilter_row(kind, line, previous, channels)
        except ValueError:
            return None
        pixels[row * stride : (row + 1) * stride] = line
        previous = bytes(line)
    return width, height, channels, bytes(pixels)


def analyse_frame(
    data: bytes, *, grid: int = PROBE_GRID, blank_ratio: float = BLANK_UNIFORM_RATIO
) -> FrameQuality:
    """Measure how much of a captured frame is a single flat colour."""
    decoded = decode_png(data)
    if decoded is None:
        return FrameQuality(decoded=False)
    width, height, channels, pixels = decoded
    stride = width * channels

    whole: Counter[tuple[int, int, int]] = Counter()
    # Alpha is dropped: a surface withheld by the compositor comes back opaque
    # grey, and a fully transparent frame is flat by the same measure anyway.
    tiles: list[Counter[tuple[int, int, int]]] = [Counter() for _ in range(grid * grid)]
    for y in range(height):
        row_band = min(grid - 1, y * grid // height)
        base = y * stride
        for x in range(width):
            offset = base + x * channels
            colour = (pixels[offset], pixels[offset + 1], pixels[offset + 2])
            whole[colour] += 1
            tiles[row_band * grid + min(grid - 1, x * grid // width)][colour] += 1

    total = width * height
    if not total or not whole:
        return FrameQuality(decoded=False)
    dominant, count = whole.most_common(1)[0]
    ratio = count / total

    flat_regions: list[str] = []
    for index, tile in enumerate(tiles):
        tile_total = sum(tile.values())
        if not tile_total:
            continue
        if tile.most_common(1)[0][1] / tile_total >= blank_ratio:
            flat_regions.append(f"r{index // grid + 1}c{index % grid + 1}")

    return FrameQuality(
        decoded=True,
        uniform_ratio=ratio,
        dominant_color="#%02x%02x%02x" % dominant,
        distinct_colors=len(whole),
        blank=ratio >= blank_ratio,
        blank_regions=tuple(flat_regions),
        region_count=grid * grid,
    )


# ---------------------------------------------------------------------------
# What the page says about itself
# ---------------------------------------------------------------------------

# Read-only, bounded, and free of field values. It answers the questions an
# agent actually has to answer between two steps — what can I act on, what is
# drawn rather than built, is this screen still settling — without ever
# returning what a person typed. Password-shaped fields are dropped whole
# rather than emptied, so no accidental future edit can start returning them.
_OBSERVE_SCRIPT = """
(() => {
  const MAX = 200;
  const isSecret = (el) => {
    if (!el) return false;
    if (el.type === 'password') return true;
    const auto = (el.getAttribute && el.getAttribute('autocomplete')) || '';
    if (/password|one-time-code/i.test(auto)) return true;
    const identity = ((el.id || '') + ' ' + ((el.getAttribute && el.getAttribute('name')) || '')
      + ' ' + ((el.getAttribute && el.getAttribute('aria-label')) || '')).toLowerCase();
    return /pass(word|wd)|secret|otp/.test(identity);
  };
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return null;
    const rect = el.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) return null;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return null;
    return rect;
  };
  const round = (rect) => ({
    x: Math.round(rect.x), y: Math.round(rect.y),
    width: Math.round(rect.width), height: Math.round(rect.height),
  });

  const controls = [];
  const selector = 'a[href],button,input,select,textarea,summary,'
    + '[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=radio]';
  for (const el of document.querySelectorAll(selector)) {
    if (controls.length >= MAX) break;
    if (isSecret(el)) continue;
    const rect = visible(el);
    if (!rect) continue;
    const tag = el.tagName.toLowerCase();
    const typed = ['input', 'textarea', 'select'].includes(tag);
    controls.push({
      tag,
      id: el.id || '',
      name: (el.getAttribute('name') || ''),
      role: (el.getAttribute('role') || ''),
      type: (el.getAttribute('type') || '').toLowerCase(),
      label: (el.getAttribute('aria-label') || '').slice(0, 120),
      // A field's *value* is never reported, only whether it currently holds
      // one: that is what tells an agent a step took effect, and it cannot
      // leak a reference number, an account, or anything else typed in.
      text: typed ? '' : ((el.innerText || el.textContent || '').trim().slice(0, 120)),
      filled: typed ? Boolean(el.value) : null,
      checked: (el.type === 'checkbox' || el.type === 'radio') ? Boolean(el.checked) : null,
      disabled: el.matches(':disabled,[aria-disabled=true]'),
      bounds: round(rect),
    });
  }

  // A drawn screen has its controls inside one big picture. Reporting the
  // surfaces separately is what stops "no controls found" from being read as
  // "nothing is on screen".
  const surfaces = [];
  for (const el of document.querySelectorAll('canvas,svg,object,embed')) {
    if (surfaces.length >= 20) break;
    const rect = visible(el);
    if (!rect) continue;
    if (rect.width * rect.height < 40000) continue;  // an icon, not a screen
    surfaces.push({ tag: el.tagName.toLowerCase(), id: el.id || '', bounds: round(rect) });
  }

  const dialogs = [];
  for (const el of document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog]')) {
    if (dialogs.length >= 10) break;
    if (!visible(el)) continue;
    dialogs.push({ id: el.id || '', text: (el.innerText || '').trim().slice(0, 200) });
  }

  return {
    title: document.title.slice(0, 200),
    readyState: document.readyState,
    viewport: { width: innerWidth, height: innerHeight },
    scroll: { x: Math.round(scrollX), y: Math.round(scrollY) },
    controls,
    control_count: controls.length,
    truncated: controls.length >= MAX,
    drawn_surfaces: surfaces,
    dialogs,
    frame_count: window.frames.length,
  };
})()
"""


@dataclass
class Observation:
    """A page described in facts an agent can act on, pixels or no pixels."""

    url: str = ""
    title: str = ""
    ready_state: str = ""
    controls: list[dict[str, Any]] = field(default_factory=list)
    drawn_surfaces: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    viewport: dict[str, int] = field(default_factory=dict)
    frame_count: int = 0
    truncated: bool = False
    error: str = ""

    @property
    def drawn_screen(self) -> bool:
        """Whether this screen is painted rather than built from controls.

        Such a screen cannot be driven from the DOM, so its evidence has to be
        a picture — which is exactly the case where a blank frame is fatal
        rather than merely unhelpful.
        """
        return bool(self.drawn_surfaces) and len(self.controls) <= 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "ready_state": self.ready_state,
            "viewport": dict(self.viewport),
            "control_count": len(self.controls),
            "controls": list(self.controls),
            "truncated": self.truncated,
            "drawn_surfaces": list(self.drawn_surfaces),
            "drawn_screen": self.drawn_screen,
            "dialogs": list(self.dialogs),
            "frame_count": self.frame_count,
            "error": self.error,
        }


def observe_page(page: Any) -> Observation:
    """Read one page's actionable structure. Never raises; failure is reported."""
    try:
        raw = page.evaluate(_OBSERVE_SCRIPT)
    except Exception as exc:
        return Observation(error=f"{type(exc).__name__}: {exc}"[:200])
    if not isinstance(raw, dict):
        return Observation(error="the page returned no structure")
    try:
        url = redact_url(page.url)
    except Exception:
        url = ""
    return Observation(
        url=url,
        title=redact_text(str(raw.get("title") or "")),
        ready_state=str(raw.get("readyState") or ""),
        controls=[c for c in (raw.get("controls") or []) if isinstance(c, dict)],
        drawn_surfaces=[s for s in (raw.get("drawn_surfaces") or []) if isinstance(s, dict)],
        dialogs=[
            {"id": str(d.get("id") or ""), "text": redact_text(str(d.get("text") or ""))}
            for d in (raw.get("dialogs") or [])
            if isinstance(d, dict)
        ],
        viewport={
            "width": int((raw.get("viewport") or {}).get("width") or 0),
            "height": int((raw.get("viewport") or {}).get("height") or 0),
        },
        frame_count=int(raw.get("frame_count") or 0),
        truncated=bool(raw.get("truncated")),
    )


# ---------------------------------------------------------------------------
# Capturing a frame, and refusing to believe a flat one
# ---------------------------------------------------------------------------

# A PNG of one flat colour cannot compress large, so its size alone rules the
# common case out without decoding anything. Only a frame below this many bytes
# per megapixel is worth measuring exactly; everything above it demonstrably
# carries detail. Being wrong here is cheap in one direction only — a false
# suspicion costs one small probe, a false reassurance would let a grey frame
# into the recording — so the threshold sits well above any flat frame's size.
SUSPICIOUS_BYTES_PER_MEGAPIXEL = 12_000

# A frame that is *partly* withheld — a grey panel over the content area, the
# rest of the window intact — compresses normally and slips past the size gate.
# Measuring exactly every so often catches one that appears mid-recording
# without paying for a probe on every single step.
DEEP_PROBE_INTERVAL = 10


def _suspicious(data: bytes, width: int, height: int) -> bool:
    """Whether a frame is small enough for its size to imply a flat picture."""
    megapixels = max(width * height, 1) / 1_000_000
    return len(data) / megapixels < SUSPICIOUS_BYTES_PER_MEGAPIXEL


class PageVision:
    """Take frames of a live page and report honestly on what they contain.

    Frames are requested through the DevTools protocol rather than through
    ``page.screenshot`` so the capture route itself can be changed when the
    normal one comes back empty:

    * ``surface`` — the composited window, which is what a person sees and the
      only route that shows plugin and video content. It is also the route a
      capture-protection agent, a lost GPU context, or an occluded window can
      quietly turn grey.
    * ``renderer`` — ``fromSurface: false``, which re-renders the page inside
      the renderer process and never touches the window's surface. Slower and
      blind to plugin content, but it returns real pixels for a page whose
      surface is being withheld.

    Once a page has needed the second route, it keeps it: the condition that
    forced the change does not usually clear between two clicks, and paying for
    a failed capture on every step would be the slowest possible way to be
    wrong.
    """

    def __init__(
        self,
        artifact_dir: Path,
        *,
        blank_ratio: float = BLANK_UNIFORM_RATIO,
        probe_scale: float = PROBE_SCALE,
        deep_interval: int = DEEP_PROBE_INTERVAL,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.blank_ratio = blank_ratio
        self.probe_scale = probe_scale
        self.deep_interval = max(1, deep_interval)
        self._sequence = 0
        self._captures = 0
        # Per page: the route that last produced real pixels, and its DevTools
        # session. Keyed by the page object itself, never by id(): once a tab
        # closes and its object is collected, CPython is free to hand the same
        # id to the next object allocated, and the next tab would then be given
        # a closed session and another tab's route preference. A closed session
        # fails quietly — every capture on that page would fall back to the
        # plain screenshot, losing blank detection and the renderer escalation
        # without a word. Holding the page keeps the id unreusable as well.
        self._preferred: dict[Any, str] = {}
        self._sessions: dict[Any, Any] = {}
        # Set once a page has been seen to withhold its surface, so the
        # recording can say so plainly instead of shipping grey evidence.
        self.blank_frames = 0
        self.escalations = 0
        # One line per frame, next to the frames themselves. Review needs to
        # know *which* screenshot is not worth looking at; without this the only
        # way to find out is to open every one of them.
        self.journal_path = self.artifact_dir / "screenshots" / "quality.jsonl"

    @property
    def frames_taken(self) -> int:
        return self._sequence

    def summary(self) -> dict[str, Any]:
        """What this recording's evidence is worth, in one small object."""
        return {
            "frames": self._sequence,
            "blank_frames": self.blank_frames,
            "escalations": self.escalations,
            "routes_used": sorted(set(self._preferred.values())),
            "surface_withheld": self.blank_frames > 0,
        }

    def _journal(self, capture: Capture) -> None:
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(capture.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # losing the index must never cost the frame it describes

    # ---------- CDP plumbing ----------

    def _session(self, page: Any) -> Any | None:
        """One cached DevTools session per page; None when CDP is unavailable.

        Test doubles and non-Chromium builds have no protocol session, and the
        caller falls back to the plain screenshot path rather than failing.
        """
        key = page
        if key in self._sessions:
            return self._sessions[key]
        session = None
        try:
            session = page.context.new_cdp_session(page)
        except Exception:
            session = None
        self._sessions[key] = session
        return session

    def _capture_bytes(self, page: Any, method: str, *, scale: float = 0.0) -> bytes:
        """Raw PNG for one route, or b"" if that route is unavailable or fails."""
        session = self._session(page)
        if session is None:
            return b""
        params: dict[str, Any] = {
            "format": "png",
            "fromSurface": method != "renderer",
            "captureBeyondViewport": False,
        }
        if scale > 0:
            viewport = page.viewport_size or {}
            width = int(viewport.get("width") or 0)
            height = int(viewport.get("height") or 0)
            if width > 0 and height > 0:
                params["clip"] = {
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": height,
                    "scale": scale,
                }
        try:
            result = session.send("Page.captureScreenshot", params)
        except Exception:
            return b""
        try:
            return base64.b64decode(result.get("data") or "")
        except Exception:
            return b""

    # ---------- the verdict ----------

    def measure(self, page: Any, method: str) -> FrameQuality:
        """Exactly measure what one route is currently returning for this page."""
        probe = self._capture_bytes(page, method, scale=self.probe_scale)
        if not probe:
            return FrameQuality(decoded=False)
        return analyse_frame(probe, blank_ratio=self.blank_ratio)

    # ---------- capturing ----------

    def capture(self, page: Any, *, deep: bool = False) -> Capture:
        """Save one frame of *page*, escalating away from a route returning grey.

        Never raises: a recording that stops because a screenshot failed has
        lost far more than the screenshot.
        """
        self._sequence += 1
        self._captures += 1
        relative = f"screenshots/{self._sequence:06d}.png"
        target = self.artifact_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return Capture(error=f"{type(exc).__name__}: {exc}"[:200])

        preferred = self._preferred.get(page, "surface")
        routes = [preferred] + [r for r in ("surface", "renderer") if r != preferred]

        first_error = ""
        fallback: tuple[bytes, str, FrameQuality] | None = None
        for index, method in enumerate(routes):
            data = self._capture_bytes(page, method)
            if not data:
                if not first_error:
                    first_error = f"{method} route returned nothing"
                continue
            quality = self._verdict(page, method, data, deep=deep)
            if not quality.blank:
                self._preferred[page] = method
                return self._write(
                    target, relative, data, method, quality, escalated=index > 0
                )
            # Flat. Keep it in case every route is flat — evidence that the
            # whole window is being withheld is worth more than no frame at all.
            if fallback is None:
                fallback = (data, method, quality)
            self.escalations += 1

        if fallback is not None:
            data, method, quality = fallback
            self.blank_frames += 1
            return self._write(
                target, relative, data, method, quality, escalated=len(routes) > 1
            )

        # No protocol route worked at all: fall back to the browser driver's own
        # screenshot, which is what a test double and a non-Chromium build have.
        try:
            page.screenshot(path=str(target), timeout=5000)
        except Exception as exc:
            return Capture(error=(first_error or f"{type(exc).__name__}: {exc}")[:200])
        return Capture(relative_path=relative, method="page", quality=None)

    def _verdict(self, page: Any, method: str, data: bytes, *, deep: bool) -> FrameQuality:
        """Measure this frame when it is cheap, suspicious, or explicitly asked for."""
        viewport = page.viewport_size or {}
        width = int(viewport.get("width") or 0) or 1
        height = int(viewport.get("height") or 0) or 1
        if deep or self._captures % self.deep_interval == 0 or _suspicious(data, width, height):
            return self.measure(page, method)
        return FrameQuality(decoded=False)

    def _write(
        self,
        target: Path,
        relative: str,
        data: bytes,
        method: str,
        quality: FrameQuality,
        *,
        escalated: bool,
    ) -> Capture:
        try:
            target.write_bytes(data)
        except OSError as exc:
            return Capture(error=f"{type(exc).__name__}: {exc}"[:200])
        capture = Capture(
            relative_path=relative,
            method=method,
            quality=quality,
            escalated=escalated,
        )
        self._journal(capture)
        return capture

    # ---------- the diagnostic ----------

    def diagnose(self, page: Any) -> dict[str, Any]:
        """Try every route on this page and say which one can actually see it.

        This is the answer to "the screenshots are grey": it names the route
        that works, or establishes that none does — which is a finding about
        the machine, not about the recording, and stops the guessing.
        """
        routes: list[dict[str, Any]] = []
        for method in ("surface", "renderer"):
            data = self._capture_bytes(page, method)
            if not data:
                routes.append({
                    "method": method,
                    "available": False,
                    "bytes": 0,
                    "quality": FrameQuality(decoded=False).to_dict(),
                })
                continue
            quality = self.measure(page, method)
            routes.append({
                "method": method,
                "available": True,
                "bytes": len(data),
                "quality": quality.to_dict(),
            })

        working = [r for r in routes if r["available"] and not r["quality"]["blank"]]
        observation = observe_page(page)
        return {
            "routes": routes,
            "working_route": working[0]["method"] if working else "",
            "protocol_available": self._session(page) is not None,
            "observation": observation.to_dict(),
            "verdict": _verdict_sentence(routes, working, observation),
        }


def _verdict_sentence(
    routes: list[dict[str, Any]], working: list[dict[str, Any]], observation: Observation
) -> str:
    """One plain sentence an operator can act on."""
    if not any(r["available"] for r in routes):
        return (
            "No screenshot route answered at all: this browser build exposes no DevTools "
            "screenshot, so evidence for this page has to come from its structure."
        )
    if working and working[0]["method"] == "surface":
        return "The normal screenshot route sees this page; no workaround is needed."
    if working:
        blocked = ", ".join(
            r["method"] for r in routes if r["available"] and r["quality"]["blank"]
        )
        return (
            f"The {blocked} route comes back blank on this page but the "
            f"{working[0]['method']} route returns real pixels; SmartOps will use that one."
        )
    partial = [
        r for r in routes if r["available"] and r["quality"].get("partially_blank")
    ]
    if partial:
        return (
            "Part of this page is being withheld from every screenshot route — "
            "the flat regions are listed per route. Screenshots stay partial evidence here."
        )
    if observation.drawn_screen:
        return (
            "Every screenshot route returns a blank frame and this screen is drawn rather "
            "than built from controls, so there is currently no usable evidence of it. "
            "Something on this machine is withholding the window's picture."
        )
    return (
        "Every screenshot route returns a blank frame. The page's structure is still "
        "readable, so steps can be recorded and checked from it while pictures cannot be trusted."
    )
