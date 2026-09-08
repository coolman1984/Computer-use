"""Ask a page what it is, before deciding how to automate it.

Every hour spent on this project so far has gone into making one sensor —
the DOM, then the screenshot — carry the whole task. The lesson from the
G-MES attempts is that no single sensor survives a corporate portal: ids are
generated, the screen is painted rather than built, the window's picture can be
withheld, and the grid is not a table. What decides whether an automation is
worth building is therefore not "did the click work" but **which sensors this
particular application actually exposes**.

That is all this module does. It opens nothing, changes nothing, and clicks
nothing. It asks the page a fixed list of questions and returns the answers as
a capability report, so the strategy for a step is chosen from evidence instead
of from hope.

Three of the questions matter more than the rest:

* **Is this a Nexacro application?** If it is, the screen has a real object
  model behind the anonymous ``<div>``s — named forms, named components, and
  grids bound to datasets. A row count that goes from 0 to 758 is stronger
  proof that a query ran than any screenshot could be, and it survives a
  machine that refuses to show the browser's picture at all.
* **Can the accessibility tree name the controls?** A screen whose ids are
  regenerated on every load can still be perfectly stable when addressed as
  "the button called Inquiry".
* **Can anything see the window?** Answered by ``vision.PageVision``, which
  measures the frame rather than trusting that one came back.

No field value, dataset cell, cookie, or credential is read. Component and
dataset *names* are structure, of the same kind as the selectors the recorder
already keeps; their contents are never touched.

Nexacro application object model (``nexacro.getApplication``), and
``Grid.getBindDataset``: https://docs.tobesoft.com/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .redaction import redact_text, redact_url
from .vision import Observation, PageVision, observe_page

# Read-only, bounded, and defensive at every step: a probe that throws on an
# unfamiliar build tells us nothing, so every lookup that might not exist is
# wrapped and reported as absent rather than allowed to fail the whole answer.
_NEXACRO_SCRIPT = """
(() => {
  const out = {
    present: false, version: '', runtime: '', application: false,
    forms: [], grids: [], exports: {}, error: '',
    form_count: 0, component_count: 0, truncated: false,
  };
  try {
    if (typeof nexacro === 'undefined' || !nexacro) return out;
    out.present = true;
    // Builds disagree about where the version lives; report whichever answers.
    for (const key of ['version', '_version', 'VERSION', 'nexacroVersion']) {
      const value = nexacro[key];
      if (typeof value === 'string' && value) { out.version = value.slice(0, 40); break; }
    }
    if (!out.version && typeof nexacro.getVersion === 'function') {
      try { out.version = String(nexacro.getVersion() || '').slice(0, 40); } catch (_) {}
    }
    out.runtime = (typeof nexacro._runtimetype === 'string' && nexacro._runtimetype)
      || (nexacro._IsRuntimeMode === false ? 'web' : '');

    for (const name of ['ExcelExportObject', 'FileDownload', 'FileDownTransfer', 'FileUpload']) {
      out.exports[name] = typeof nexacro[name] === 'function' || typeof nexacro[name] === 'object';
    }

    const app = typeof nexacro.getApplication === 'function' ? nexacro.getApplication() : null;
    if (!app) return out;
    out.application = true;

    const typeOf = (component) => {
      try {
        return String(component._type_name || (component.constructor && component.constructor.name) || '')
          .slice(0, 40);
      } catch (_) { return ''; }
    };
    const label = (component) => {
      try {
        const text = component.text || component.value || '';
        return typeof text === 'string' ? text.slice(0, 60) : '';
      } catch (_) { return ''; }
    };

    // Walk frames depth-first for the forms they hold. Caps everywhere: an
    // application with a runaway frame tree must not hang the recording.
    const forms = [];
    const seen = new Set();
    const visit = (node, depth) => {
      if (!node || depth > 8 || forms.length >= 40 || seen.has(node)) return;
      seen.add(node);
      try {
        if (node.form && node.form.components) forms.push(node.form);
        const frames = node.frames || node.getFrames?.() || null;
        const count = frames ? (frames.length || 0) : 0;
        for (let i = 0; i < count && i < 40; i++) visit(frames[i], depth + 1);
        if (node.form) visit(node.form, depth + 1);
      } catch (_) { /* an unfamiliar node shape is simply not walked */ }
    };
    visit(app.mainframe || app, 0);

    for (const form of forms) {
      const components = [];
      let all = 0;
      try {
        const collection = form.components;
        all = collection.length || 0;
        for (let i = 0; i < all; i++) {
          if (components.length >= 60) { out.truncated = true; break; }
          const component = collection[i];
          if (!component) continue;
          const kind = typeOf(component);
          components.push({
            id: String(component.id || component.name || '').slice(0, 120),
            type: kind,
            text: label(component),
            visible: component.visible !== false,
            enabled: component.enable !== false,
          });
          if (kind.toLowerCase().includes('grid')) {
            let dataset = null;
            try { dataset = component.getBindDataset ? component.getBindDataset() : null; } catch (_) {}
            out.grids.push({
              id: String(component.id || '').slice(0, 120),
              // Names and shape only. No cell is ever read.
              dataset: dataset ? String(dataset.id || dataset.name || '').slice(0, 120) : '',
              rows: dataset && dataset.getRowCount ? Number(dataset.getRowCount()) : null,
              columns: dataset && dataset.getColCount ? Number(dataset.getColCount()) : null,
            });
          }
        }
      } catch (_) { /* a form that will not enumerate is reported empty */ }
      out.component_count += components.length;
      out.forms.push({
        id: String(form.id || form.name || '').slice(0, 120),
        component_total: all,
        components,
      });
    }
    out.form_count = out.forms.length;
  } catch (error) {
    out.error = String(error && error.message || error).slice(0, 200);
  }
  return out;
})()
"""

# How addressable this page is without any framework knowledge: whether its ids
# look stable or generated, and whether controls carry an accessible name. A
# screen of `ext-gen-4417` ids with no labels is one that only a framework
# sensor or a physical gesture can drive reliably, and it is better to know that
# during the recording than during the third failed replay.
_ADDRESSABILITY_SCRIPT = """
(() => {
  const generated = /(^|[^a-z])(ext-gen|gwt-uid|yui_|mat-|:r[0-9a-z]+:|[0-9a-f]{8}-[0-9a-f]{4})/i;
  const selector = 'a[href],button,input,select,textarea,[role=button],[role=link],'
    + '[role=menuitem],[role=tab],[role=checkbox],[role=radio]';
  let total = 0, withId = 0, stableId = 0, named = 0, disabled = 0;
  for (const el of document.querySelectorAll(selector)) {
    const rect = el.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) continue;
    if (++total > 400) break;
    if (el.id) {
      withId++;
      if (!generated.test(el.id)) stableId++;
    }
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim()
      || (el.innerText || '').trim();
    if (name) named++;
    if (el.matches(':disabled,[aria-disabled=true]')) disabled++;
  }
  // Requests the page has already made, counted by kind. Enough to answer
  // "does this screen talk to a server at all" without touching one body.
  let xhr = 0;
  try {
    for (const entry of performance.getEntriesByType('resource')) {
      if (entry.initiatorType === 'xmlhttprequest' || entry.initiatorType === 'fetch') xhr++;
    }
  } catch (_) {}
  return {
    controls: total, with_id: withId, stable_id: stableId,
    named: named, disabled: disabled, xhr_requests: xhr,
    frames: window.frames.length,
  };
})()
"""


@dataclass
class SensorResult:
    """One sensor's answer, in the same shape whatever the sensor is."""

    name: str
    status: str  # "strong" | "partial" | "absent" | "blocked"
    detail: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor": self.name,
            "status": self.status,
            "detail": self.detail,
            "facts": dict(self.facts),
        }


def _evaluate(page: Any, script: str) -> tuple[dict[str, Any], str]:
    """Run one read-only script; ("", error) rather than an exception on failure."""
    try:
        value = page.evaluate(script)
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    return (value if isinstance(value, dict) else {}), ""


def nexacro_sensor(page: Any) -> SensorResult:
    """Whether this screen has a Nexacro object model behind it, and how much of it."""
    raw, error = _evaluate(page, _NEXACRO_SCRIPT)
    if error:
        return SensorResult("nexacro", "absent", f"The page could not be asked: {error}")
    if not raw.get("present"):
        return SensorResult(
            "nexacro", "absent", "This is not a Nexacro application; the DOM is the ground truth here."
        )
    if not raw.get("application"):
        return SensorResult(
            "nexacro",
            "partial",
            "Nexacro is loaded but its application object is not reachable from the page, "
            "so components cannot be named. Only the DOM view of this screen is usable.",
            facts={"version": raw.get("version", ""), "exports": raw.get("exports", {})},
        )

    forms = [
        {
            "id": redact_text(str(form.get("id") or "")),
            "component_total": int(form.get("component_total") or 0),
            "components": [
                {
                    "id": redact_text(str(component.get("id") or "")),
                    "type": str(component.get("type") or "")[:40],
                    "text": redact_text(str(component.get("text") or "")),
                    "visible": bool(component.get("visible")),
                    "enabled": bool(component.get("enabled")),
                }
                for component in (form.get("components") or [])
                if isinstance(component, dict)
            ],
        }
        for form in (raw.get("forms") or [])
        if isinstance(form, dict)
    ]
    grids = [
        {
            "id": redact_text(str(grid.get("id") or "")),
            "dataset": redact_text(str(grid.get("dataset") or "")),
            "rows": grid.get("rows"),
            "columns": grid.get("columns"),
        }
        for grid in (raw.get("grids") or [])
        if isinstance(grid, dict)
    ]
    bound = [grid for grid in grids if grid["dataset"]]
    exports = {k: bool(v) for k, v in (raw.get("exports") or {}).items()}

    if bound:
        detail = (
            f"Nexacro is driving this screen and {len(bound)} of its {len(grids)} grids are bound "
            "to a named dataset. A dataset's row count is direct proof that a query ran, which is "
            "the strongest evidence available here and does not depend on seeing the screen."
        )
        status = "strong"
    elif forms:
        detail = (
            "Nexacro is driving this screen and its components can be named, but no grid is bound "
            "to a dataset on this page. Components give stable identity; success still has to be "
            "proved another way."
        )
        status = "strong"
    else:
        detail = (
            "Nexacro is loaded and its application answers, but no form enumerated on this screen. "
            "Try again once the working screen is open rather than the shell."
        )
        status = "partial"

    return SensorResult(
        "nexacro",
        status,
        detail,
        facts={
            "version": str(raw.get("version") or ""),
            "runtime": str(raw.get("runtime") or ""),
            "form_count": len(forms),
            "component_count": int(raw.get("component_count") or 0),
            "truncated": bool(raw.get("truncated")),
            "forms": forms,
            "grids": grids,
            "export_objects": exports,
            "excel_export_available": bool(exports.get("ExcelExportObject")),
        },
    )


def accessibility_sensor(page: Any) -> SensorResult:
    """Whether Chrome can name this screen's controls independently of its markup.

    An application whose ids are regenerated on every load is still addressable
    as "the button called Inquiry", so this decides whether a semantic locator
    is available as the first fallback or not available at all.
    """
    try:
        session = page.context.new_cdp_session(page)
    except Exception as exc:
        return SensorResult(
            "accessibility",
            "blocked",
            "The DevTools protocol did not attach, so the accessibility tree cannot be read "
            f"({type(exc).__name__}). On a corporate machine this is usually policy.",
        )
    try:
        session.send("Accessibility.enable")
        tree = session.send("Accessibility.getFullAXTree")
    except Exception as exc:
        return SensorResult(
            "accessibility",
            "blocked",
            f"The accessibility tree was refused: {type(exc).__name__}.",
        )

    interesting = {
        "button", "link", "textbox", "combobox", "checkbox", "radio",
        "menuitem", "tab", "grid", "row", "gridcell", "treeitem",
    }
    named: dict[str, int] = {}
    unnamed = 0
    for node in tree.get("nodes") or []:
        role = ((node.get("role") or {}).get("value") or "").lower()
        if role not in interesting:
            continue
        name = ((node.get("name") or {}).get("value") or "").strip()
        if name:
            named[role] = named.get(role, 0) + 1
        else:
            unnamed += 1

    total_named = sum(named.values())
    if total_named >= 5:
        status, detail = "strong", (
            f"Chrome can name {total_named} controls on this screen, so steps can be addressed by "
            "what they are called rather than by a generated id."
        )
    elif total_named:
        status, detail = "partial", (
            f"Only {total_named} controls carry an accessible name. Semantic locators will work for "
            "some steps and not others."
        )
    else:
        status, detail = "absent", (
            "No control on this screen carries an accessible name, so nothing here can be addressed "
            "semantically."
        )
    return SensorResult(
        "accessibility",
        status,
        detail,
        facts={"named_by_role": named, "named_total": total_named, "unnamed": unnamed},
    )


def addressability_sensor(page: Any) -> SensorResult:
    """How findable this screen's controls are from the markup alone."""
    raw, error = _evaluate(page, _ADDRESSABILITY_SCRIPT)
    if error:
        return SensorResult("dom", "blocked", f"The page could not be asked: {error}")
    controls = int(raw.get("controls") or 0)
    stable = int(raw.get("stable_id") or 0)
    named = int(raw.get("named") or 0)
    facts = {
        "controls": controls,
        "with_id": int(raw.get("with_id") or 0),
        "stable_id": stable,
        "named": named,
        "frames": int(raw.get("frames") or 0),
        "xhr_requests": int(raw.get("xhr_requests") or 0),
    }
    if not controls:
        return SensorResult(
            "dom",
            "absent",
            "The markup exposes no visible controls at all. Either this screen is painted rather "
            "than built, or it has not finished loading.",
            facts=facts,
        )
    share = stable / controls
    if share >= 0.6:
        status, detail = "strong", (
            f"{stable} of {controls} visible controls carry an id that looks stable across loads."
        )
    elif share > 0:
        status, detail = "partial", (
            f"Only {stable} of {controls} visible controls have a stable-looking id; the rest are "
            "generated and will move between loads."
        )
    else:
        status, detail = "absent", (
            f"None of the {controls} visible controls has a stable id. Locators built from this "
            "markup will not survive a reload."
        )
    return SensorResult("dom", status, detail, facts=facts)


def visual_sensor(page: Any, vision: PageVision) -> SensorResult:
    """Whether anything on this machine can actually see the browser window."""
    report = vision.diagnose(page)
    route = report.get("working_route") or ""
    routes = report.get("routes") or []
    facts = {"routes": routes, "working_route": route}
    if not report.get("protocol_available"):
        return SensorResult("visual", "blocked", report.get("verdict", ""), facts=facts)
    if route == "surface":
        return SensorResult("visual", "strong", report.get("verdict", ""), facts=facts)
    if route:
        return SensorResult("visual", "partial", report.get("verdict", ""), facts=facts)
    return SensorResult("visual", "blocked", report.get("verdict", ""), facts=facts)


# The order steps should be addressed in, strongest identity first. Each entry
# is (name, the sensor that has to be usable, what it buys). Nothing is chosen
# because it is clever; each one is chosen only when the sensor above it cannot
# answer, which is what stops a run from silently falling back to a coordinate.
_LADDER: tuple[tuple[str, str, str], ...] = (
    ("nexacro_component", "nexacro", "the component's own name inside the application"),
    ("accessible_name", "accessibility", "what the control is called"),
    ("stable_selector", "dom", "an id or name that survives a reload"),
    ("element_relative_point", "visual", "a press at a fraction of a known element"),
)

# What can prove a step worked, in the same order. A recording whose only proof
# is a picture is exactly the recording that fails on a machine where the
# picture is withheld — which is why the dataset ranks above it.
#
# The fourth field is whether the proof applies to any step. A completed
# download proves exactly one step, the one that produced the file; letting it
# stand as the general answer would leave every other step in the recording
# with no check at all, which is how a run reports success after clicking
# through a form it never filled in.
_EVIDENCE: tuple[tuple[str, str, str, bool], ...] = (
    ("dataset_row_count", "nexacro", "a bound grid's row count changing", True),
    ("download_completed", "download", "a file arriving and validating", False),
    ("control_state", "accessibility", "a control becoming enabled, named or visible", True),
    ("selector_visible", "dom", "an element appearing", True),
    ("screenshot", "visual", "a picture of the screen", True),
)

_USABLE = {"strong", "partial"}


def probe_page(page: Any, vision: PageVision) -> dict[str, Any]:
    """Ask one open page every question that decides how it can be automated.

    Read-only from end to end. The result is a capability report: what each
    sensor can see, which identity and which proof to build steps on, and the
    honest gaps.

    The caller supplies the lens rather than this creating one, because the
    only sensible lens is the one that already belongs to something — a live
    recording's, or the one the diagnostic command made. Diagnosis measures
    frames without saving any, so it never writes to that lens's directory.
    """
    sensors = [
        nexacro_sensor(page),
        accessibility_sensor(page),
        addressability_sensor(page),
        visual_sensor(page, vision),
    ]
    by_name = {sensor.name: sensor for sensor in sensors}
    observation = observe_page(page)

    # A download is not a sensor that can be probed without performing one, so
    # it is reported as available-in-principle: the browser context accepts
    # downloads, and that is what the recorder relies on.
    identity = [
        {"strategy": name, "available": by_name.get(sensor, SensorResult(sensor, "absent")).status in _USABLE,
         "buys": buys}
        for name, sensor, buys in _LADDER
    ]
    evidence = [
        {
            "proof": name,
            # A download cannot be probed without performing one, so it is
            # reported as available in principle: the recorder accepts
            # downloads and validates the bytes that arrive.
            "available": True if sensor == "download" else (
                by_name.get(sensor, SensorResult(sensor, "absent")).status in _USABLE
            ),
            "applies_to": "any step" if general else "the step that produces the file",
            "buys": buys,
        }
        for name, sensor, buys, general in _EVIDENCE
    ]
    chosen_identity = next((item["strategy"] for item in identity if item["available"]), "")
    chosen_evidence = next(
        (
            item["proof"]
            for item in evidence
            if item["available"] and item["applies_to"] == "any step"
        ),
        "",
    )

    try:
        url = redact_url(page.url)
    except Exception:
        url = ""

    return {
        "url": url,
        "title": observation.title,
        "sensors": [sensor.to_dict() for sensor in sensors],
        "identity_ladder": identity,
        "evidence_ladder": evidence,
        "recommended_identity": chosen_identity,
        "recommended_evidence": chosen_evidence,
        "drawn_screen": observation.drawn_screen,
        "observation": observation.to_dict(),
        "verdict": _capability_sentence(by_name, chosen_identity, chosen_evidence, observation),
    }


def _capability_sentence(
    sensors: dict[str, SensorResult],
    identity: str,
    evidence: str,
    observation: Observation,
) -> str:
    """The one paragraph an operator should read before deciding to record."""
    visual_blocked = (
        "visual" in sensors and sensors["visual"].status == "blocked"
    )
    withheld = (
        "The window's picture is not available on this machine, so screenshots are not evidence "
        "here — every check has to come from the page's own state"
    )
    if not identity:
        blocked_note = f" {withheld}." if visual_blocked else ""
        return (
            "Nothing on this screen can be addressed reliably: it has no framework object model, "
            "no accessible names, and no stable ids." + blocked_note + " Recording it would produce "
            "an automation that cannot be replayed. Open the working screen and probe again before "
            "spending a recording on it."
        )
    lines = [f"Steps here should be identified by {identity.replace('_', ' ')}"]
    if evidence:
        lines.append(f"and proved by {evidence.replace('_', ' ')}")
    else:
        lines.append("but nothing on this screen can prove a step worked, which is not recordable yet")
    if visual_blocked:
        lines.append(withheld)
    nexacro = sensors.get("nexacro")
    if nexacro is not None and nexacro.facts.get("excel_export_available"):
        lines.append(
            "The application exposes Nexacro's Excel export object, so a download can be traced from "
            "the export it came from rather than inferred from a click"
        )
    if observation.drawn_screen:
        lines.append(
            "This screen is painted rather than built, so a press has to be reproduced as a gesture "
            "on fresh geometry"
        )
    return ". ".join(lines) + "."
