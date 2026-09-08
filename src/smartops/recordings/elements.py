"""One place where each control on a screen is described, so it is repaired once.

Until now every step carried its own private copy of how to find its element.
Twelve steps touching the same Inquiry button held twelve locator lists, and a
site that renamed that button broke twelve steps that had to be repaired twelve
times — each one a separate chance to get it slightly wrong. Worse, nothing
connected them: there was no way to ask "what does this automation actually
depend on?", because the answer was scattered across the steps.

This is the answer commercial automation platforms reached long ago, and it is
the same shape: an application holds screens, a screen holds elements, and a
step refers to an element by name instead of restating how to find it. Repair
the element and every step that uses it is repaired.

Two rules keep it honest:

* **A step never depends on this file existing.** Its own locators stay on it,
  exactly as before. The repository is consulted first because it is the
  freshest description of a control; when it is missing, or has nothing for
  this element, the step falls back to what it recorded and behaves as it
  always did. A recording made before any of this still replays.
* **Nothing here decides anything.** It stores descriptions and returns them.
  Choosing that a renamed control is "the same" control is a person's call, and
  the review screen is where that call is made.

Nothing sensitive is stored: locators, an element's own description, and the
words next to it — the same facts the recorder already keeps on a step, and
never a field's value.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# The file is versioned so a later shape change can be recognised rather than
# guessed at. A repository written by a newer SmartOps is left alone instead of
# being rewritten into a shape it does not use.
REPOSITORY_VERSION = 1


def screen_key(url: str) -> str:
    """Which screen a URL belongs to, ignoring what varies between visits.

    A report portal puts the period, the run id, and the operator's filters in
    the query string, so keying on the whole URL would make every visit a new
    screen and the repository would never accumulate anything. The route — host
    and path — is what identifies the screen a person would name.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unknown"
    route = f"{parts.hostname or ''}{parts.path or ''}".strip("/")
    return route or "unknown"


def element_key(locators: list[str], fingerprint: dict[str, Any]) -> str:
    """A stable name for one control, derived from what it is rather than where.

    Built from the strongest locator the recorder found plus what the control is
    called, so the same button met again in a later recording lands on the same
    entry instead of creating a second one. Deliberately not a hash of
    everything: a control that gains an aria-label is still that control, and a
    key that changed whenever anything about it changed would defeat the point.
    """
    primary = next((value for value in locators if value), "")
    name = str(fingerprint.get("name") or fingerprint.get("anchor") or "")
    role = str(fingerprint.get("role") or fingerprint.get("tag") or "")
    seed = f"{role}|{name}|{primary}".lower()
    # A short, readable digest: long enough not to collide across a screen's
    # controls, short enough to appear in a step and still be legible.
    digest = 0
    for character in seed:
        digest = (digest * 131 + ord(character)) & 0xFFFFFFFF
    readable = "".join(ch for ch in (name or role or "el").lower() if ch.isalnum())[:20]
    return f"{readable or 'el'}-{digest:08x}"


@dataclass
class Element:
    """One control, as every step that touches it will find it."""

    key: str
    screen: str
    locators: list[str] = field(default_factory=list)
    anchors: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    # How many recorded steps refer to this control. A control used by one step
    # and a control used by nine are worth different amounts of care when one
    # of them stops resolving.
    used_by: int = 0
    # Filled by the check that runs when a recording ends: whether this control
    # could still be found, and how many things matched it.
    last_check: dict[str, Any] = field(default_factory=dict)

    @property
    def reference(self) -> str:
        """How a step names this element: screen and key together."""
        return f"{self.screen}#{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "screen": self.screen,
            "locators": list(self.locators),
            "anchors": [dict(anchor) for anchor in self.anchors],
            "fingerprint": dict(self.fingerprint),
            "used_by": self.used_by,
            "last_check": dict(self.last_check),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Element":
        return cls(
            key=str(raw.get("key") or ""),
            screen=str(raw.get("screen") or ""),
            locators=[str(item) for item in (raw.get("locators") or []) if item],
            anchors=[dict(item) for item in (raw.get("anchors") or []) if isinstance(item, dict)],
            fingerprint=dict(raw.get("fingerprint") or {}),
            used_by=int(raw.get("used_by") or 0),
            last_check=dict(raw.get("last_check") or {}),
        )


class ElementRepository:
    """Every control one system's automations depend on, in one file.

    Held in memory while a recording runs and written to disk when it ends, the
    way the rest of a recording's evidence is. Safe to use from the recorder's
    browser thread and the web application at the same time.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._elements: dict[str, Element] = {}
        self._lock = threading.Lock()

    # ---------- reading ----------

    def __len__(self) -> int:
        return len(self._elements)

    def __iter__(self):
        return iter(list(self._elements.values()))

    def get(self, reference: str) -> Element | None:
        return self._elements.get(reference)

    def locators_for(self, reference: str) -> list[str]:
        """The current best ways to find this control, freshest first.

        This is what a run consults before falling back to the locators frozen
        into its own plan: a control repaired in review is repaired here, and
        every step that names it picks the repair up without being edited.
        """
        element = self._elements.get(reference)
        return list(element.locators) if element else []

    def on_screen(self, screen: str) -> list[Element]:
        return [element for element in self._elements.values() if element.screen == screen]

    # ---------- writing ----------

    def remember(
        self,
        *,
        url: str,
        locators: list[str],
        fingerprint: dict[str, Any] | None = None,
        anchors: list[dict[str, Any]] | None = None,
    ) -> Element:
        """Register the control this step touched, and answer with it.

        Meeting a control again updates what is known about it rather than
        adding a second entry — a later recording that finds a better locator
        for the same button improves every automation that uses it.
        """
        described = dict(fingerprint or {})
        clean = [str(item) for item in locators if item and item != "[redacted]"]
        screen = screen_key(url)
        key = element_key(clean, described)
        reference = f"{screen}#{key}"
        with self._lock:
            element = self._elements.get(reference)
            if element is None:
                element = Element(key=key, screen=screen)
                self._elements[reference] = element
            # Newly-seen locators are added at the end: the order a step was
            # recorded with is the order that step proved, and a later sighting
            # has no standing to promote itself above it.
            for value in clean:
                if value not in element.locators:
                    element.locators.append(value)
            if described:
                element.fingerprint = described
            if anchors:
                element.anchors = [dict(anchor) for anchor in anchors]
            element.used_by += 1
            return element

    def record_check(self, reference: str, *, found: int, error: str = "") -> None:
        """Note whether this control could still be found, and how uniquely."""
        with self._lock:
            element = self._elements.get(reference)
            if element is None:
                return
            element.last_check = {
                "found": int(found),
                # One match is a control an automation can act on. None means
                # the step is already broken; several means it is ambiguous,
                # which fails just as surely but for the opposite reason.
                "resolves": found == 1,
                "error": error[:200],
            }

    def repair(self, reference: str, locators: list[str]) -> Element | None:
        """Replace how this control is found, everywhere at once.

        The whole reason the repository exists. A person decides in review that
        the renamed button is the same button, says so once, and every step that
        names this element is repaired — instead of twelve separate edits, each
        one a fresh chance to get it slightly wrong.
        """
        clean = [str(item).strip() for item in locators if str(item).strip()]
        if not clean:
            return None
        with self._lock:
            element = self._elements.get(reference)
            if element is None:
                return None
            element.locators = clean
            element.last_check = {}  # the old verdict describes the old locators
            return element

    # ---------- persistence ----------

    def to_dict(self) -> dict[str, Any]:
        screens: dict[str, dict[str, Any]] = {}
        for element in self._elements.values():
            screens.setdefault(element.screen, {})[element.key] = element.to_dict()
        return {"version": REPOSITORY_VERSION, "screens": screens}

    def load(self) -> "ElementRepository":
        """Read what is on disk, keeping anything already in memory."""
        if not self.path or not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self  # an unreadable repository is a lost optimisation, not a failure
        if int(raw.get("version") or 0) > REPOSITORY_VERSION:
            return self  # written by a newer SmartOps; leave it exactly as it is
        with self._lock:
            for screen, elements in (raw.get("screens") or {}).items():
                if not isinstance(elements, dict):
                    continue
                for key, described in elements.items():
                    if not isinstance(described, dict):
                        continue
                    element = Element.from_dict({**described, "key": key, "screen": screen})
                    self._elements.setdefault(element.reference, element)
        return self

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # losing the shared description must never cost the recording


def merge(into: ElementRepository, source: ElementRepository) -> ElementRepository:
    """Fold one recording's elements into the system's shared repository."""
    for element in source:
        into.remember(
            url=element.screen,
            locators=element.locators,
            fingerprint=element.fingerprint,
            anchors=element.anchors,
        )
    return into
