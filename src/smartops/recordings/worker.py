"""Headed Chrome capture worker: watches a whole human task, not just its clicks.

It never records typed secrets, cookies, or response bodies.

What a person does to a web application is mostly not clicking. They type into
fields, choose from lists, press Enter, wait for something to appear, work in a
panel that is really an iframe, and end up with a tab they did not open on
purpose. A click log describes none of that, and an automation built from one
replays an empty form against the wrong page.

Everything here is wired to the **context**, not to a page, so a popup or a
second tab is captured exactly like the first one — corporate portals open SSO
and download confirmations in new windows as a matter of routine.

Two rules shape what gets written down:

* **A password is captured as a reference, never as a value.** The recording
  goes into the database and onto a review screen; a secret in it would be a
  secret in both. The real value is fetched from the credential store during the
  run and exists only for that instant.
* **Every step carries the evidence of its own success**, chosen while the page
  is in front of us: the element that appeared, the value that changed, the tab
  that opened. Deciding that later, from a click log, is guesswork.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..adapters.browser.authentication import ensure_authenticated
from ..adapters.browser.session import open_browser_context
from ..config import BrowserSettings
from ..credentials import CredentialStore
from .confidence import score_step
from .redaction import redact_selector, redact_text, redact_url, safe_network_summary
from .probe import probe_page
from .timeline import ActionObservation, Timeline
from .vision import PageVision, observe_page

# Runs inside every recorded page and every frame. It listens in the CAPTURING
# phase (the `true` third argument) so it sees the event before the page's own
# handlers have reacted to it, and reports through an exposed binding rather
# than a polled global: a poll samples once a tick and silently drops repeats of
# the same action within it, while a binding fires once per real event, in order.
#
# Every listener below is bound to `window`, not `document`. A window opened
# with `window.open()` can run this whole script exactly once, against a
# transient `about:blank` document, and never again once the real page has
# navigated in: the window survives that navigation, its document does not.
# A listener attached to `document` at that moment is attached to a document
# nobody will ever act in again, and the popup's own sign-in, typed field, and
# confirm button are recorded as if none of it happened. `window` is the outer
# node in the capturing chain regardless of which document currently lives
# under it — capture always runs window -> document -> ... -> target — so a
# window-level listener sees the same events, slightly earlier, and keeps
# working across that swap. `document` itself is still read from inside every
# handler (`document.querySelectorAll`, `document.styleSheets`, and the lazy
# `ensureObserving()` below): a lookup made at event time resolves whichever
# document is actually live then, which is the document that matters.
#
# Selectors are attribute selectors — `[id="…"]` via JSON.stringify — rather than
# CSS's `#id` shorthand, which breaks on any id containing a dot or colon
# (routine in Nexacro-style frameworks, e.g. "mainframe.vFrameSet1.form.grid").
# A bare `#mainframe.vFrameSet1…` parses as id "mainframe" plus bogus classes and
# points replay at the wrong element entirely.
_CAPTURE_SCRIPT = """
(() => {
  // A window that keeps running (see the note above) must not run this whole
  // script a second time: every listener below would be installed twice on
  // the same persistent window, and every action from then on would be
  // reported twice. A window this has never run on has no flag yet, so the
  // one install a new page — popup or not — actually needs is never the one
  // this guard skips.
  if (window.__smartopsCaptureInstalled) return;
  window.__smartopsCaptureInstalled = true;

  const q = (v) => JSON.stringify(v);

  // The element the person actually touched. Inside a web component the browser
  // reports the *host* as the event target — click a button in a shadow root and
  // e.target is the custom element wrapping it, which is useless as a locator
  // and often not clickable at all. The composed path starts at the real node,
  // so it is the truth wherever the shadow root is open. A closed one reveals
  // nothing to anybody, and the host is then recorded honestly as what we saw.
  const realTarget = (e) => {
    try {
      const path = e.composedPath && e.composedPath();
      if (path && path.length && path[0] && path[0].nodeType === 1) return path[0];
    } catch (_) { /* not a composed event */ }
    return e.target;
  };

  // An id a framework made up this morning. Recording one as the primary way to
  // find an element is how an automation passes its test run and fails the next
  // day: the selector is perfectly valid and matches nothing. These ids are
  // still kept, but last, behind every identity that means something.
  const GENERATED_ID = /(ext-gen|gwt-uid|yui_|__BVID__|:r[0-9a-z]+:|[0-9a-f]{8}-[0-9a-f]{4}-|\\d{7,})/i;

  // What the control is, when the markup does not say. A page of anonymous divs
  // with generated ids is still perfectly addressable as "the button called
  // Inquiry" — which is the only identity some enterprise screens ever offer.
  const IMPLICIT_ROLE = {
    a: 'link', button: 'button', select: 'combobox', textarea: 'textbox',
    summary: 'button', h1: 'heading', h2: 'heading', h3: 'heading',
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute && el.getAttribute('role');
    if (explicit) return explicit.trim().split(/\\s+/)[0];
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return { checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button',
               reset: 'button', search: 'searchbox' }[type] || 'textbox';
    }
    if (tag === 'a' && !el.getAttribute('href')) return '';
    return IMPLICIT_ROLE[tag] || '';
  };
  const accessibleName = (el) => {
    const aria = (el.getAttribute && el.getAttribute('aria-label')) || '';
    if (aria.trim()) return aria.trim();
    const title = (el.getAttribute && el.getAttribute('title')) || '';
    if (title.trim()) return title.trim();
    const text = (el.innerText || el.textContent || '').trim();
    // A whole panel's text is not a name. Only something short enough to be a
    // label is worth addressing an element by.
    return text.length > 0 && text.length <= 60 ? text.replace(/\\s+/g, ' ') : '';
  };

  // Several ways to find the same element, strongest identity first. Replay
  // tries them in order, so a page that regenerates its ids between releases can
  // still be driven by name, by test id, or by what its controls are called.
  function locatorFor(el) {
    const out = { strategy: 'css', value: '', fallbacks: [] };
    if (!el || !el.tagName) return out;
    const add = (sel) => { if (sel && !out.fallbacks.includes(sel)) out.fallbacks.push(sel); };
    const generated = el.id && GENERATED_ID.test(el.id);
    if (el.id && !generated) add('[id=' + q(el.id) + ']');
    const name = el.getAttribute && el.getAttribute('name');
    if (name) add('[name=' + q(name) + ']');
    const testId = el.getAttribute && (el.getAttribute('data-testid') || el.getAttribute('data-test'));
    if (testId) add('[data-testid=' + q(testId) + ']');
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria) add('[aria-label=' + q(aria) + ']');
    const role = roleOf(el), label = accessibleName(el);
    if (role && label) add('role=' + role + '[name=' + q(label) + ']');
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.getAttribute('href')) add('a[href=' + q(el.getAttribute('href')) + ']');
    if (label && !role) add('text=' + q(label));
    // Last, and only because a wrong-looking id still beats no locator at all
    // when every meaningful identity is missing.
    if (generated) add('[id=' + q(el.id) + ']');
    out.value = out.fallbacks[0] || '';
    out.fallbacks = out.fallbacks.slice(1);
    return out;
  }

  // Some web applications (notably canvas-like Nexacro screens) expose one
  // large DOM surface for many controls. Clicking the surface's centre would
  // repeat the wrong action, so keep the click as a fraction of that element.
  // This scales with the element at any desktop resolution and never stores an
  // absolute screen coordinate.
  function relativePoint(el, event, viewportWidth, viewportHeight) {
    if (!el || !el.getBoundingClientRect) return {};
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return {};
    const tag = (el.tagName || '').toLowerCase();
    const drawnSurface = tag === 'canvas' || tag === 'svg';
    const largeSurface = rect.width / viewportWidth >= 0.30 && rect.height / viewportHeight >= 0.15;
    if (!drawnSurface && !largeSurface) return {};
    const clamp = (value) => Math.max(0, Math.min(1, value));
    return {
      elementX: clamp((event.clientX - rect.left) / rect.width),
      elementY: clamp((event.clientY - rect.top) / rect.height),
      relativeToElement: true,
    };
  }

  // Return the part of the saved credential this login field needs. Password
  // and autocomplete are the strongest signals. A small exact-name allowlist
  // covers corporate SSO pages (including userNameInput) that omit
  // autocomplete, without treating ordinary fields containing "user" as
  // credentials.
  function credentialField(el) {
    if (!el) return '';
    if (el.type === 'password') return 'password';
    const auto = (el.getAttribute && el.getAttribute('autocomplete')) || '';
    if (/current-password|new-password|one-time-code/i.test(auto)) return 'password';
    if (/^username$/i.test(auto)) return 'username';
    const raw = ((el.id || '') + ' ' + ((el.getAttribute && el.getAttribute('name')) || ''));
    const names = raw.split(/\\s+/).map(v => v.replace(/[^a-z0-9]/gi, '').toLowerCase());
    const loginNames = new Set([
      'username', 'usernameinput', 'usernamefield', 'userid', 'useridinput',
      'useridentifier', 'loginid', 'loginidinput', 'loginname', 'loginnameinput',
      'accountname', 'accountnameinput',
    ]);
    return names.some(name => loginNames.has(name)) ? 'username' : '';
  }

  // A safe, bounded snapshot of controls that are actually visible now. It
  // deliberately contains selectors only — never element text, values, HTML,
  // cookies, or page URLs. The compiler compares snapshots around a click to
  // turn a newly-visible result marker into direct success evidence.
  function observableLocators() {
    const output = [], seen = new Set();
    const visible = (el) => {
      if (!el || !el.getBoundingClientRect) return false;
      const rect = el.getBoundingClientRect();
      if (!(rect.width > 0 && rect.height > 0)) return false;
      const style = getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden';
    };
    const candidates = document.querySelectorAll(
      '[id],[name],[data-testid],[data-test],[aria-label]'
    );
    for (const el of candidates) {
      if (output.length >= 200) break;
      if (!visible(el) || credentialField(el)) continue;
      const locator = locatorFor(el);
      if (!locator.value || seen.has(locator.value)) continue;
      seen.add(locator.value);
      output.push(locator);
    }
    return output;
  }
  window.__smartopsObservableLocators = observableLocators;

  const report = (payload) => {
    try { window.__smartopsReport(payload); } catch (_) { /* recording ended */ }
  };

  // What has been typed into a field but not yet committed. "change" is the
  // right event to record — it fires once, with the finished value, instead of
  // once per keystroke — but on a text input it only fires on blur, and a person
  // who types and then presses Enter never blurs it. So the latest value is held
  // here and flushed by whichever comes first: the change event, losing focus, or
  // any other action being recorded. Flushing before another action is what keeps
  // the steps in the order they really happened.
  let pending = null;
  let pendingEl = null;
  // What has already been written down for each field. A text input reports its
  // value twice — once when the person moves on and once when the browser fires
  // change on blur — and without this the same typing lands in the recording
  // twice, so replay types it, types it again, and the step numbering no longer
  // matches what the person did.
  const committed = new WeakMap();

  function flushPending() {
    if (!pending) return;
    const p = pending, el = pendingEl;
    pending = null;
    pendingEl = null;
    if (el && committed.get(el) === p.value && !p.secret) return;
    if (el) committed.set(el, p.value);
    report(p);
  }

  // Lets the recorder commit anything still being typed when the person stops
  // the recording. Without it, a value typed into the last field and never
  // followed by another action would simply not be in the recording.
  window.__smartopsFlush = flushPending;

  function rememberFill(el) {
    // Something else was being typed and has not been written down yet: commit
    // it first, so the steps stay in the order they really happened.
    if (pendingEl && pendingEl !== el) flushPending();
    const credential = credentialField(el);
    const secret = Boolean(credential);
    const details = describe(el);
    pendingEl = el;
    pending = {
      action: 'fill',
      locator: locatorFor(el),
      // Only a non-secret value travels. For a secret the platform records that
      // something must be typed here and where to get it at run time.
      value: secret ? '' : fieldValue(el),
      secret: secret,
      credentialField: credential,
      ...details,
      // describe() normally uses the field value as its human-readable label.
      // That is useful for ordinary fields, but would send a password through
      // the binding even though `value` above is empty.
      text: secret ? '' : details.text,
    };
  }

  // What is currently in a field, wherever the browser keeps it.
  const fieldValue = (el) => {
    if (!el) return '';
    if (el.isContentEditable) return (el.innerText || el.textContent || '');
    return el.value || '';
  };

  const describe = (el) => ({
    tag: el && el.tagName ? el.tagName.toLowerCase() : '',
    text: (el && (el.innerText || el.value || '') || '').slice(0, 80),
  });

  window.addEventListener('input', (e) => {
    const el = realTarget(e);
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    // A rich-text editor is a div the person types into. It has no `value`, so
    // the old check skipped it entirely and the recording came back with the
    // click that focused the editor and nothing that was written in it.
    if (el.isContentEditable) { rememberFill(el); return; }
    if (tag !== 'input' && tag !== 'textarea') return;
    if (el.type === 'checkbox' || el.type === 'radio') return;
    rememberFill(el);
  }, true);

  window.addEventListener('blur', () => flushPending(), true);

  // Some component libraries act on mousedown and re-render the pressed node
  // before the mouse button comes back up. Nexacro's organisation tree does
  // this: the checkbox toggles on the press, the tree redraws, and the browser
  // never fires a click event for the gesture — or fires it on some ancestor
  // that says nothing about which box was ticked. That is how a recording of
  // G-MES lost the "tick VD" step and its replay asked for a report with no
  // organisation selected. So the press is remembered here: a matching
  // release with no click event shortly after is reported as the click it was,
  // and a click that lands on an ancestor of the pressed node is reported
  // against the node the person actually pressed.
  // ---- a menu that only exists while the pointer is on its trigger ----
  //
  // A person hovers "Reports", the menu opens, they click "Daily". The click is
  // the only thing an event log sees, and replaying it alone finds nothing:
  // by then the menu is closed. So two cheap facts are kept — where the pointer
  // last rested, and when each element appeared — and a click on something that
  // appeared just after that hover reports the hover first, as the step it was.
  //
  // The observer only stamps a time on mutated nodes; it never reads their
  // content, and it does nothing at all on a page that is not changing.
  //
  // It has to be armed lazily rather than once, up front: a document read at
  // the top of this script, before the guard above even ran once for this
  // window, can be the same transient `about:blank` document that makes the
  // rest of this file worth reading. Observing it would watch something
  // nobody can ever act in. So the live `document` is re-checked — a plain
  // reference comparison, cheap enough to repeat on every hover — right
  // before anything that needs `appearedAt`, and the observer is re-armed
  // only when that document has actually changed underneath it.
  const appearedAt = new WeakMap();
  let observedDocument = null;
  function ensureObserving() {
    if (observedDocument === document) return;
    observedDocument = document;
    try {
      new MutationObserver((records) => {
        const now = Date.now();
        for (const record of records) {
          if (record.type === 'childList') {
            for (const node of record.addedNodes) {
              if (node && node.nodeType === 1) appearedAt.set(node, now);
            }
          } else if (record.target && record.target.nodeType === 1) {
            appearedAt.set(record.target, now);
          }
        }
      }).observe(document, {
        subtree: true, childList: true, attributes: true,
        attributeFilter: ['style', 'class', 'hidden', 'aria-hidden', 'aria-expanded'],
      });
    } catch (_) { /* a document that refuses observation simply loses this hint */ }
  }
  // The ordinary case — this script running once, against the document it
  // will always act in — should not have to wait for a hover to start seeing
  // reveals, so it is armed once here too.
  ensureObserving();

  // A menu can open two ways, and only one of them touches the DOM. A script
  // that toggles a class is caught by the observer above; a stylesheet rule
  // like `.trigger:hover + .menu { display: block }` changes nothing at all —
  // no attribute, no node, no event. Watching for mutations alone therefore
  // misses the most common hover menu on the web. So the page's own stylesheets
  // are asked instead: is this element's appearance written as depending on a
  // hover somewhere? A rule is matched with its `:hover` removed, which is
  // exactly the element the rule is about.
  let hoverRules = null;
  let hoverRuleSheets = -1;
  function collectHoverRules() {
    const sheets = document.styleSheets;
    if (hoverRules !== null && hoverRuleSheets === sheets.length) return hoverRules;
    hoverRules = [];
    hoverRuleSheets = sheets.length;
    for (const sheet of sheets) {
      let rules = null;
      // A stylesheet from another origin refuses to be read. That is the
      // browser's rule, not ours; those pages simply lose this hint.
      try { rules = sheet.cssRules; } catch (_) { continue; }
      for (const rule of rules || []) {
        const selector = rule && rule.selectorText;
        if (!selector || selector.indexOf(':hover') === -1) continue;
        for (const part of selector.split(',')) {
          const bare = part.replace(/:hover/g, '').trim();
          if (bare) hoverRules.push(bare);
        }
      }
    }
    return hoverRules;
  }
  const appearanceDependsOnHover = (el) => {
    for (const selector of collectHoverRules()) {
      try { if (el.matches(selector)) return true; } catch (_) { /* unsupported selector */ }
    }
    return false;
  };

  // Where the pointer has rested recently. A trail rather than one element,
  // because reaching a menu item means passing over the menu itself, and the
  // trigger is two or three resting places back by the time it is clicked.
  const hoverTrail = [];
  const REVEAL_WINDOW_MS = 3000;
  window.addEventListener('mouseover', (e) => {
    // Rearmed here, not only once at the top: this is the event that most
    // reliably runs before the click a reveal is evidence for, so it is the
    // last safe place to notice the live document changed underneath a stale
    // observer before that click needs what the observer would have seen.
    ensureObserving();
    const el = realTarget(e);
    if (!el || !el.tagName) return;
    const last = hoverTrail[hoverTrail.length - 1];
    if (last && last.el === el) return;
    hoverTrail.push({ el: el, at: Date.now() });
    if (hoverTrail.length > 8) hoverTrail.shift();
  }, true);

  // The container the clicked thing lives in, if that container only exists
  // because of a hover — either because it appeared just now, or because the
  // stylesheet says its appearance is conditional on one.
  function revealedRootOf(clicked, since) {
    let node = clicked;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const stamp = appearedAt.get(node);
      if ((stamp && stamp >= since) || appearanceDependsOnHover(node)) return node;
    }
    return null;
  }

  function reportRevealingHover(clicked) {
    if (!clicked || !clicked.tagName || !hoverTrail.length) return;
    const now = Date.now();
    const oldest = now - REVEAL_WINDOW_MS;
    const root = revealedRootOf(clicked, oldest);
    if (!root) return;
    // Walk back through the resting places, past the revealed menu itself, to
    // the last thing the pointer sat on that was not part of what it revealed.
    for (let i = hoverTrail.length - 1; i >= 0; i--) {
      const entry = hoverTrail[i];
      if (entry.at < oldest) break;
      const el = entry.el;
      if (!el.isConnected) continue;
      if (el === clicked || root.contains(el) || el.contains(clicked)) continue;
      hoverTrail.length = 0;
      report({
        action: 'hover',
        locator: locatorFor(el),
        observedVisibleLocators: observableLocators(),
        ...describe(el),
      });
      return;
    }
  }

  // Dragging one thing onto another. The press/release pair above deliberately
  // discards a gesture that moved, because that is how a text selection or a
  // stray hand looks — but a real drag then vanished from the recording without
  // a word, and the automation quietly did nothing where a person had moved a
  // column or dropped a row. The browser's own drag events say plainly when a
  // drag was a drag, so the two ends are recorded together as one step.
  let dragSource = null;
  window.addEventListener('dragstart', (e) => {
    const el = realTarget(e);
    if (!el || !el.tagName) return;
    dragSource = { el: el, locator: locatorFor(el), at: Date.now(), ...describe(el) };
  }, true);

  window.addEventListener('drop', (e) => {
    const target = realTarget(e);
    const source = dragSource;
    dragSource = null;
    if (!source || !target || !target.tagName) return;
    if (Date.now() - source.at > 30000) return;  // a stale start, not this drop
    flushPending();
    report({
      action: 'drag',
      locator: source.locator,
      dropLocator: locatorFor(target),
      observedVisibleLocators: observableLocators(),
      text: source.text,
      tag: source.tag,
    });
  }, true);

  // A right-click opens something a left-click never will. Recorded as its own
  // action rather than dropped, so a task that needs one is repeatable and a
  // plan that cannot repeat one says so instead of clicking the wrong way.
  window.addEventListener('contextmenu', (e) => {
    const el = realTarget(e);
    if (!el || !el.tagName) return;
    flushPending();
    const w = innerWidth || 1, h = innerHeight || 1;
    report({ ...clickPayload(el, e, w, h), action: 'context_click', replayAction: 'context_click' });
  }, true);

  const clickPayload = (el, e, w, h) => ({
    action: 'click',
    locator: locatorFor(el),
    observedVisibleLocators: observableLocators(),
    x: e.clientX / w, y: e.clientY / h,
    ...relativePoint(el, e, w, h),
    ...describe(el),
  });
  let press = null;
  let pressTimer = null;
  const PRESS_MATCH_PX = 8;
  const CLICK_GRACE_MS = 250;

  window.addEventListener('mousedown', (e) => {
    const pressed = realTarget(e);
    if (e.button !== 0 || !pressed || !pressed.tagName) return;
    clearTimeout(pressTimer);
    const w = innerWidth || 1, h = innerHeight || 1;
    // The locator is taken now, while the pressed node is still in the DOM.
    press = { el: pressed, path: e.composedPath(), at: Date.now(), x: e.clientX, y: e.clientY,
              payload: { ...clickPayload(pressed, e, w, h), replayAction: 'pointer_click' } };
  }, true);

  window.addEventListener('mouseup', (e) => {
    if (!press || e.button !== 0) return;
    const p = press;
    const moved = Math.abs(e.clientX - p.x) > PRESS_MATCH_PX ||
                  Math.abs(e.clientY - p.y) > PRESS_MATCH_PX;
    if (moved || Date.now() - p.at > 1500) { press = null; return; }  // a drag, not a click
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {
      if (press !== p) return;  // a click event took care of it
      press = null;
      flushPending();
      report(p.payload);
    }, CLICK_GRACE_MS);
  }, true);

  window.addEventListener('click', (e) => {
    clearTimeout(pressTimer);
    const p = press;
    press = null;
    flushPending();
    const el = realTarget(e);
    const w = innerWidth || 1, h = innerHeight || 1;
    // A page that never fires mouseover before this click — a keyboard-only
    // activation, say — still deserves its best current reveal evidence
    // rather than whatever a stale observer happened to collect.
    ensureObserving();
    reportRevealingHover(el);
    if (p && p.el !== el && p.path && p.path.includes(el)) {
      // The browser settled on an ancestor from the original event path after
      // the pressed node was replaced.  ``el.contains(p.el)`` is no longer
      // reliable because p.el is detached, so use the transient press-time
      // path and keep the node locator the person actually pressed.
      report(p.payload);
      return;
    }
    report(clickPayload(el, e, w, h));
  }, true);

  // "change" rather than "input": it fires once, when the person has finished,
  // instead of once per keystroke. A per-keystroke log would record a hundred
  // steps for one typed reference and leak the value character by character.
  window.addEventListener('change', (e) => {
    const el = realTarget(e);
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      // Whatever was being typed happened first and belongs on the record
      // first; discarding it here lost the value entirely.
      flushPending();
      // A list the person can pick several things from reports only its
      // *first* selection through `value`. A report screen filtered by three
      // plants would replay filtered by one, and produce a smaller report that
      // looks perfectly valid — the worst kind of wrong. So every chosen option
      // travels, and a single-choice list keeps its existing shape unchanged.
      const chosen = Array.from(el.selectedOptions || []);
      report({
        action: 'select',
        locator: locatorFor(el),
        value: el.value,
        values: el.multiple ? chosen.map((option) => option.value) : null,
        text: chosen.map((option) => option.text).join(', ').slice(0, 80),
      });
      return;
    }
    if (tag === 'input' || tag === 'textarea') {
      if (el.type === 'checkbox' || el.type === 'radio') {
        flushPending();
        report({ action: 'check', locator: locatorFor(el), checked: !!el.checked, ...describe(el) });
        return;
      }
      rememberFill(el);
      flushPending();
    }
  }, true);

  // Only keys that mean something on their own. Recording every keystroke would
  // both bury the real steps and capture whatever was being typed.
  const MEANINGFUL = new Set(['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'PageDown', 'PageUp']);
  const MODIFIERS = new Set(['Control', 'Shift', 'Alt', 'Meta']);
  window.addEventListener('keydown', (e) => {
    if (MODIFIERS.has(e.key)) return;
    const combo = e.ctrlKey || e.altKey || e.metaKey;
    if (!combo && !MEANINGFUL.has(e.key)) return;
    // Whatever was typed goes on the record before the key that acts on it.
    flushPending();
    const parts = [];
    if (e.ctrlKey) parts.push('Control');
    if (e.altKey) parts.push('Alt');
    if (e.metaKey) parts.push('Meta');
    if (e.shiftKey) parts.push('Shift');
    parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
    const focused = realTarget(e);
    report({
      action: 'press', locator: locatorFor(focused), key: parts.join('+'),
      observedVisibleLocators: observableLocators(), ...describe(focused)
    });
  }, true);
})();
"""


class PlaywrightRecordingWorker:
    """Runs the recording browser and turns what happens in it into steps."""

    def __init__(
        self,
        recording_id: str,
        artifact_dir: Path,
        start_url: str,
        on_step: Callable[[dict], None],
        on_heartbeat: Callable[[], None],
        on_finished: Callable[[str | None], None],
        executable_path: str = "",
        session_state_path: Path | None = None,
        headless: bool = False,
        on_ready: Callable[[Any], None] | None = None,
        system_key: str = "",
        auth_filters: dict[str, Any] | None = None,
        credential_store: CredentialStore | None = None,
        browser_settings: BrowserSettings | None = None,
    ) -> None:
        self.recording_id, self.artifact_dir, self.start_url = recording_id, artifact_dir, start_url
        # The one saved session for this system — the same file the connection
        # test, the test run and every scheduled run use. Recording in its own
        # private profile meant signing in twice and possibly recording as a
        # different account than the automation would later run as. Read in and
        # written back out, so a sign-in done here serves later runs too.
        self.session_state_path = session_state_path
        # Empty means "use the installed Google Chrome"; a configured path lets a
        # machine without Chrome record with whatever Chromium it does have.
        self.executable_path = executable_path
        # Headed for a real recording; see BrowserSettings.record_headless for
        # why running without a screen is possible at all.
        self.headless = headless
        # Called on this worker's own thread once the first page has loaded.
        # Playwright's sync objects belong to the thread that created them, so
        # this is the only place anything can drive the recorded page. Unused in
        # normal operation, where the hands belong to a person.
        self.on_ready = on_ready
        self.system_key = system_key
        self.auth_filters = dict(auth_filters or {})
        self.credential_store = credential_store
        self.browser_settings = browser_settings or BrowserSettings(
            executable_path=executable_path,
            record_headless=headless,
        )
        self.on_step, self.on_heartbeat, self.on_finished = on_step, on_heartbeat, on_finished
        # Frames are taken through this rather than through page.screenshot so a
        # picture that comes back blank is noticed and re-taken by a route that
        # does not read the window's surface. See recordings/vision.py.
        self.vision = PageVision(artifact_dir)
        # One observation per emitted step, tying its before/after picture, its
        # visible-locator diff and its proof candidates together. A sidecar next
        # to steps.jsonl, not a replacement for it — see recordings/timeline.py.
        self.timeline = Timeline(artifact_dir)
        # Steps scored weak as they are captured, so a person recording live can
        # be told to redo one while it is still cheap to. Bounded because this is
        # a live signal for the current recording, not a history worth keeping.
        self._weak_steps: list[dict[str, Any]] = []
        self._stop, self._paused = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._shot_seq = 0  # screenshot counter, unique within one recording
        # FrameQuality.to_dict() for the frame paired with self._last_shot, and
        # for whatever _shoot() most recently captured. None is an ordinary
        # value for both, not a sign anything failed: PageVision only measures a
        # frame's quality every PageVision.deep_interval captures (see
        # vision.py), so most frames simply carry no verdict at all.
        self._last_shot_quality: dict[str, Any] | None = None
        self._last_capture_quality: dict[str, Any] | None = None
        # The first page the recorder opens. Exposed so a test can drive the same
        # page a person would be clicking in, rather than a second browser.
        self.primary_page: Any = None
        self._pages: list[Any] = []
        # Every tab's name, kept past the tab's own life — see _page_name for why
        # a popup that closes itself would otherwise take its steps' identity
        # with it. Keyed by the page object, which also keeps it alive long
        # enough that nothing else can be mistaken for it.
        self._page_names: dict[Any, str] = {}
        self._cdp_sessions: list[Any] = []
        self._download_count = 0
        # Events arrive from inside Playwright's own dispatch — a page binding
        # firing during a click, a download starting during that same click. Any
        # Playwright call made from there re-enters the sync API on a call that
        # has not returned yet, and the whole recorder deadlocks. So handlers do
        # the cheapest possible thing (read already-cached values, queue the
        # event) and every protocol call — screenshots, titles, saving a
        # download — happens on the worker's own loop below.
        self._events: queue.Queue = queue.Queue()
        # Questions from outside the browser thread — "what is on screen right
        # now?" — answered on the loop below for the same reason events are
        # drained there: only this thread may touch Playwright objects. The
        # asker waits on its own event rather than polling.
        self._requests: queue.Queue = queue.Queue()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"recording-{self.recording_id}", daemon=True
        )
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- naming a page and a frame ----------

    def _page_name(self, page: Any) -> str:
        """A stable name for a tab, so replay can return to the same one.

        "main" is the tab the task started in. Everything else is numbered in the
        order it opened, which is how replay can follow a popup and come back.

        The name is remembered the first time it is worked out, because a tab's
        identity has to outlive the tab. A corporate sign-in popup closes itself
        the moment it succeeds, and the steps performed inside it are still
        queued when it goes: by the time they are written down the page is no
        longer in the open list, and everything the person did in that window
        would be filed against "latest" — a name that means whichever tab
        happens to be frontmost during replay, which is not where any of it
        happened.
        """
        if page is self.primary_page:
            return "main"
        remembered = self._page_names.get(page)
        if remembered:
            return remembered
        try:
            index = self._pages.index(page)
        except ValueError:
            return "latest"
        name = f"page-{index}"
        self._page_names[page] = name
        return name

    @staticmethod
    def _frame_selector(frame: Any, page: Any) -> str:
        """Which iframe a step happened in, "" for the page's own document.

        Recorded as the frame's URL rather than its element: the element selector
        belongs to the parent document and is not always available from inside
        the frame, whereas the URL identifies it from either side.
        """
        try:
            if frame is None or frame == page.main_frame:
                return ""
            return redact_url(frame.url)
        except Exception:
            return ""

    # ---------- the browser thread ----------

    def _run(self) -> None:
        network: list[dict] = []
        try:
            from playwright.sync_api import sync_playwright

            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            for item in ("screenshots", "downloads", "network", "trace", "session"):
                (self.artifact_dir / item).mkdir(exist_ok=True)

            with sync_playwright() as p:
                session = open_browser_context(
                    p,
                    self.browser_settings,
                    headless=self.headless,
                    executable_path=self.executable_path,
                    accept_downloads=True,
                    storage_state_path=self.session_state_path,
                )
                context = session.context
                try:
                    self._capture(context, network)
                finally:
                    self._preserve(context)
                    session.close()
            (self.artifact_dir / "network" / "sanitized-summary.json").write_text(
                json.dumps(network, ensure_ascii=False), encoding="utf-8"
            )
            self.on_finished(None)
        except Exception as exc:
            self.on_finished(f"Recorder failed: {type(exc).__name__}: {exc}")

    def _capture(self, context: Any, network: list[dict]) -> None:
        first = self._open_and_authenticate(context)

        # Authentication is deliberately complete before any capture facility
        # is installed. Credential values therefore cannot enter a screenshot,
        # trace, network summary, page binding, or recorded step.
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        # Context-level: applies to every page and every frame this context ever
        # opens, including popups created after this point.
        context.add_init_script(_CAPTURE_SCRIPT)
        context.on(
            "request",
            lambda request: network.append(
                safe_network_summary(request.method, request.url, request.resource_type)
            ),
        )
        context.expose_binding("__smartopsReport", self._handle_event)
        context.on("page", self._track_page)

        self._track_page(first)
        self._install_capture_on_loaded_page(first)
        self._last_shot = self._shoot(first, deep=True)
        self._last_shot_quality = self._last_capture_quality

        if self.on_ready is not None:
            self.on_ready(first)

        while not self._stop.is_set():
            # Playwright's synchronous API dispatches page bindings and browser
            # events while an API call is in progress. A plain threading wait
            # leaves human clicks queued inside Playwright until Stop triggers
            # the next page call, so the web monitor appears stuck at zero.
            # Pump one live page briefly; callbacks only enqueue work, and the
            # drain below persists it outside Playwright's dispatch stack.
            self._pump_browser_events()
            self._drain(limit=50)
            self._serve_requests()
            self.on_heartbeat()
        # A value typed into the last field and never followed by another action
        # is still part of the task; ask every page to commit what it is holding.
        self._flush_pages()
        # Whatever arrived while we were stopping still belongs in the recording.
        self._drain(limit=500)

    def _open_and_authenticate(self, context: Any) -> Any:
        """Open the entry page and finish saved-credential login before capture."""
        first = context.pages[0] if context.pages else context.new_page()
        self.primary_page = first
        self._prevent_debugger_pauses(first)
        first.goto(self.start_url, wait_until="domcontentloaded", timeout=30000)
        if self.auth_filters:
            authenticated = [first]
            message = ensure_authenticated(
                context,
                first,
                system=self.system_key,
                filters=self.auth_filters,
                credential_store=self.credential_store,
                session_state_path=self.session_state_path,
                manage_tracing=False,
                pause_guard=lambda _context, page: self._prevent_debugger_pauses(page),
                on_authenticated_page=lambda page: authenticated.__setitem__(0, page),
            )
            if message:
                self._write_auth_diagnostic(context, message)
                raise RuntimeError(message)
            first = authenticated[0]
            self.primary_page = first
        return first

    def _write_auth_diagnostic(self, context: Any, message: str) -> None:
        """Save only safe routing/control facts when the SSO handoff fails."""
        login_selector = self.auth_filters.get("login_selector")
        pages: list[dict[str, Any]] = []
        for index, page in enumerate(list(getattr(context, "pages", []))):
            try:
                parsed = urlsplit(page.url)
                route = f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"
                login_visible = bool(
                    login_selector
                    and page.locator(login_selector).count() > 0
                    and page.locator(login_selector).first.is_visible()
                )
                ui = page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                          s.display !== 'none' && s.visibility !== 'hidden';
                      };
                      const nodes = Array.from(document.querySelectorAll('body *'));
                      return {
                        noticeTitles: nodes.filter((el) => visible(el) &&
                          (el.innerText || el.textContent || '').trim() === 'Notice').length,
                        closeControlIds: nodes.filter((el) => visible(el) && el.id &&
                          /closebutton|btnclose|btn_close/i.test(el.id))
                          .slice(0, 12).map((el) => el.id.slice(-120))
                      };
                    }
                    """
                )
                pages.append(
                    {
                        "index": index,
                        "route": route,
                        "login_visible": login_visible,
                        "notice_titles": int(ui.get("noticeTitles") or 0),
                        "close_control_ids": list(ui.get("closeControlIds") or []),
                    }
                )
            except Exception as exc:
                pages.append({"index": index, "unavailable": type(exc).__name__})
        target = self.artifact_dir / "session" / "auth-diagnostic.json"
        target.write_text(
            json.dumps(
                {
                    "error_stage": message,
                    "page_count": len(pages),
                    "pages": pages,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _install_capture_on_loaded_page(page: Any) -> None:
        """Apply the init script to the page loaded before capture was enabled."""
        try:
            page.evaluate(_CAPTURE_SCRIPT)
        except Exception:
            pass
        for frame in getattr(page, "frames", []):
            try:
                if frame != page.main_frame:
                    frame.evaluate(_CAPTURE_SCRIPT)
            except Exception:
                pass

    def _pump_browser_events(self) -> None:
        """Give Playwright a short dispatch window for live human actions."""
        for page in reversed(list(self._pages)):
            try:
                if page.is_closed():
                    continue
                page.wait_for_timeout(200)
                return
            except Exception:
                continue
        self._stop.wait(0.2)

    def _flush_pages(self) -> None:
        for page in list(self._pages):
            try:
                page.evaluate("() => window.__smartopsFlush && window.__smartopsFlush()")
                for frame in page.frames:
                    try:
                        frame.evaluate("() => window.__smartopsFlush && window.__smartopsFlush()")
                    except Exception:
                        pass
            except Exception:
                pass  # a page that has already closed has nothing left to commit

    def _drain(self, *, limit: int) -> None:
        """Process queued events on this thread, where Playwright calls are safe."""
        for _ in range(limit):
            try:
                kind, item = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "step":
                    self._finish_step(item)
                elif kind == "emit":
                    self._emit(item)
                else:
                    self._finish_download(item)
            except Exception:
                pass  # one lost step must not end the recording

    # ---------- answering "what is on screen now?" ----------

    def look(self, *, capture: bool = True, timeout: float = 6.0) -> dict[str, Any]:
        """Describe the tab the person is working in, from any thread.

        This is what lets an assistant follow a recording as it happens instead
        of reading it afterwards. It is strictly read-only: it reports the page
        and takes a picture of it, and can do nothing else to the browser.
        """
        if not self.alive():
            return {"available": False, "reason": "the recorder is not running"}
        answered = threading.Event()
        box: dict[str, Any] = {}
        self._requests.put((bool(capture), box, answered))
        if not answered.wait(timeout):
            return {
                "available": False,
                "reason": "the recording browser did not answer in time; it is usually "
                "busy loading a page",
            }
        return box.get("result") or {"available": False, "reason": "no answer"}

    def _serve_requests(self) -> None:
        """Answer queued questions here, where Playwright calls are legal."""
        while True:
            try:
                capture, box, answered = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                if capture in ("diagnose", "probe"):
                    page = self._active_page()
                    if page is None:
                        box["result"] = {
                            "available": False,
                            "reason": "the recording has no open tab",
                        }
                    elif capture == "diagnose":
                        box["result"] = {"available": True, **self.vision.diagnose(page)}
                    else:
                        box["result"] = {"available": True, **probe_page(page, self.vision)}
                else:
                    box["result"] = self._describe_now(bool(capture))
            except Exception as exc:
                box["result"] = {
                    "available": False,
                    "reason": f"{type(exc).__name__}: {exc}"[:200],
                }
            finally:
                answered.set()

    def _active_page(self) -> Any | None:
        """The tab in front of the person: the newest one still open."""
        for page in reversed(list(self._pages)):
            try:
                if not page.is_closed():
                    return page
            except Exception:
                continue
        return None

    def _describe_now(self, capture: bool) -> dict[str, Any]:
        page = self._active_page()
        if page is None:
            return {"available": False, "reason": "the recording has no open tab"}
        frame = self.vision.capture(page) if capture else None
        return {
            "available": True,
            "recording_id": self.recording_id,
            "paused": self._paused.is_set(),
            "page": self._page_name(page),
            "pages": [
                {"name": self._page_name(item), "url": redact_url(_safe_url(item))}
                for item in list(self._pages)
            ],
            "observation": observe_page(page).to_dict(),
            "frame": frame.to_dict() if frame else None,
            "evidence": self.vision.summary(),
            "downloads": self._download_count,
            # Recent steps whose identity or evidence is not good enough to
            # trust yet, so a person recording live can redo one immediately
            # instead of finding out during review. See recordings/confidence.py.
            "weak_steps": list(self._weak_steps[-5:]),
        }

    def diagnose_vision(self, timeout: float = 20.0) -> dict[str, Any]:
        """Run the full capture diagnosis against the live recording tab."""
        return self._ask("diagnose", timeout)

    def capabilities(self, timeout: float = 30.0) -> dict[str, Any]:
        """Ask the live recording tab how it can be automated at all.

        Read-only. Worth running on the real screen *before* recording it: it
        reports which sensors this application exposes, so a recording is only
        made once there is something that can identify a step and something
        that can prove it worked.
        """
        return self._ask("probe", timeout)

    def _ask(self, kind: str, timeout: float) -> dict[str, Any]:
        if not self.alive():
            return {"available": False, "reason": "the recorder is not running"}
        answered = threading.Event()
        box: dict[str, Any] = {}
        self._requests.put((kind, box, answered))
        if not answered.wait(timeout):
            return {"available": False, "reason": "the recording browser did not answer in time"}
        return box.get("result") or {"available": False, "reason": "no answer"}

    def _preserve(self, context: Any) -> None:
        """Save what we captured even if the session ended badly."""
        try:
            context.storage_state(path=str(self.artifact_dir / "session" / "storage-state.json"))
        except Exception:
            pass
        # Write back to the shared session too, so a sign-in performed inside the
        # recording window is the same session later runs will use.
        if self.session_state_path:
            try:
                Path(self.session_state_path).parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(self.session_state_path))
            except Exception:
                pass
        try:
            context.tracing.stop(path=str(self.artifact_dir / "trace" / "trace.zip"))
        except Exception:
            pass
        # What the evidence in this recording is actually worth. Written here so
        # it exists even for a recording that ended badly — a session that could
        # not be seen at all is exactly the one somebody needs to be told about.
        try:
            (self.artifact_dir / "screenshots").mkdir(parents=True, exist_ok=True)
            (self.artifact_dir / "screenshots" / "vision-summary.json").write_text(
                json.dumps(self.vision.summary(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------- page and download tracking ----------

    def _track_page(self, page: Any) -> None:
        if page in self._pages:
            return  # new_page() also fires the "page" event; do not double-bind
        self._pages.append(page)
        self._prevent_debugger_pauses(page)
        self._bind_dialogs(page)
        self._bind_downloads(page)
        page.on("close", lambda: self._pages.remove(page) if page in self._pages else None)
        if page is not self.primary_page and self.primary_page is not None:
            # A tab the task opened is itself a step: replay has to know to follow
            # it, and later steps refer to it by the name assigned here.
            #
            # Queued rather than emitted directly, because everything else is
            # queued. Emitting here put the "a tab opened" step *before* the click
            # that opened it — and replay then tried to switch to a tab that
            # nothing had opened yet.
            self._events.put(("emit", {
                "kind": "switch_page",
                "action": "switch_page",
                "target": {"page": self._page_name(page), "frame": ""},
                "inputs": {},
                "locator": {},
                "success": {"type": "page_available"},
                "retry": {"max_attempts": 3, "safe_to_repeat": True},
                "page_url_redacted": redact_url(_safe_url(page)),
                "target_text_redacted": "a new tab opened",
            }))

    def _prevent_debugger_pauses(self, page: Any) -> None:
        """Keep corporate SSO anti-debug scripts from freezing popup tabs.

        Playwright controls Chrome through the DevTools Protocol. Some SSO
        pages execute a ``debugger`` statement when they detect that protocol,
        which leaves Chrome showing "Debugger paused in another tab" instead
        of the login form. Apply the protocol's skip-pause setting to every
        page, including popups, and resume once in case the page paused before
        the context-level ``page`` event reached us.

        Playwright CDP session:
        https://playwright.dev/python/docs/api/class-browsercontext#browser-context-new-cdp-session
        Chrome Debugger.setSkipAllPauses:
        https://chromedevtools.github.io/devtools-protocol/tot/Debugger/#method-setSkipAllPauses
        """
        try:
            session = page.context.new_cdp_session(page)
            session.send("Debugger.setSkipAllPauses", {"skip": True})
            self._cdp_sessions.append(session)
            try:
                session.send("Debugger.resume")
            except Exception:
                pass  # the normal case: this page was not paused yet
        except Exception:
            # Recording still works on non-Chromium test doubles or when a
            # browser build does not expose CDP; only pause protection is lost.
            pass

    def _bind_dialogs(self, page: Any) -> None:
        """Answer the browser's own dialogs, and write down that we did.

        Playwright dismisses every alert, confirm and prompt automatically when
        nothing is listening. During a headed recording that is invisible and
        wrong: the person clicks Export, a confirmation they never see is
        cancelled for them, and the recording contains a click that produced
        nothing. Worse, ``beforeunload`` is dismissed too, so a page can refuse
        to leave and the recorder simply appears stuck.

        So the dialog is accepted — the answer that lets the demonstrated task
        continue — and recorded as its own step carrying the message, because a
        confirmation somebody agreed to is part of the task and belongs in
        front of a reviewer, not hidden in the browser's default behaviour.
        """

        def on_dialog(dialog: Any) -> None:
            kind = getattr(dialog, "type", "") or ""
            message = getattr(dialog, "message", "") or ""
            try:
                dialog.accept()
            except Exception:
                pass  # already handled, or the page went away with it open
            if self._paused.is_set():
                return
            self._events.put(("emit", {
                "kind": "dialog",
                "action": "dialog",
                "target": {"page": self._page_name(page), "frame": ""},
                "locator": {},
                "inputs": {"dialog_type": str(kind)[:40], "decision": "accept"},
                # The dialog appearing at all is the proof; it is the click
                # before it that has to be repeated for this to happen again.
                "success": {"type": "none"},
                "retry": {"max_attempts": 1, "safe_to_repeat": False},
                "page_url_redacted": redact_url(_safe_url(page)),
                "target_text_redacted": redact_text(str(message)),
            }))

        page.on("dialog", on_dialog)

    def _bind_downloads(self, page: Any) -> None:
        def on_download(download: Any) -> None:
            # A download usually starts *during* the click that caused it, so
            # saving it here would re-enter Playwright mid-call. Queue it.
            if self._paused.is_set():
                return
            self._events.put(("download", (download, page, _safe_url(page))))

        page.on("download", on_download)

    def _finish_download(self, item: tuple) -> None:
        download, page, page_url = item
        name = download.suggested_filename or f"download-{self._download_count + 1}"
        directory = self.artifact_dir / "downloads"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        suffix = 2
        while target.exists():
            target = directory / f"{Path(name).stem}-{suffix}{Path(name).suffix}"
            suffix += 1
        download.save_as(str(target))
        if not (target.exists() and target.stat().st_size > 0):
            return
        self._download_count += 1
        self._emit({
            "kind": "download",
            "action": "download",
            "target": {"page": self._page_name(page), "frame": ""},
            "locator": {},
            "inputs": {"file_name": target.name},
            "download_ref": f"downloads/{target.name}",
            # A file arriving is its own proof; nothing else to check.
            "success": {"type": "download_started"},
            # Never repeat a download on its own: the click that caused it is the
            # step that retries, and only when repeating it is safe.
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
            "page_url_redacted": redact_url(page_url),
            "target_text_redacted": redact_text(name),
        })

    # ---------- turning a browser event into a step ----------

    def _handle_event(self, source: dict, payload: dict) -> None:
        """Runs inside Playwright's dispatch: read cached values only, then queue.

        `page.url` and `frame.url` are cached attributes, so they are safe here
        and are read now rather than later — by the time the queue is drained the
        page may already have navigated somewhere else.
        """
        if self._paused.is_set():
            return  # dropped, not queued — matches the existing pause behaviour
        try:
            page = source["page"]
            frame = source.get("frame")
            self._events.put((
                "step", (payload, page, _safe_url(page), self._frame_selector(frame, page), frame)
            ))
        except Exception:
            pass

    def _finish_step(self, item: tuple) -> None:
        payload, page, page_url, frame_selector, frame = item
        try:
            action = payload.get("replayAction") or payload.get("action") or "click"
            locator = payload.get("locator") or {}
            before = getattr(self, "_last_shot", "")
            before_quality = self._last_shot_quality
            after = self._shoot(page)
            # The address is read a second time here, on the worker's own loop,
            # because the one on the step was read while the event was still
            # being dispatched — before the page had any chance to react. A
            # click that navigates therefore shows the *old* address on its own
            # step and the new one on whatever step comes next, which would
            # credit the navigation to the wrong action. Reading it again after
            # the frame is taken keeps a route change attached to the click that
            # caused it.
            url_after = redact_url(_safe_url(page))
            after_quality = self._last_capture_quality
            if after:
                self._last_shot = after
                self._last_shot_quality = after_quality
            # Events are queued before the page reacts; this second sample is
            # taken on the worker's control loop, never in the binding callback.
            # For a human's delayed next action, that next event's before-sample
            # provides the same evidence during compilation.
            payload["observedVisibleLocatorsAfter"] = self._observable_locators(frame or page)

            step: dict[str, Any] = {
                "kind": action,
                "action": action,
                "target": {"page": self._page_name(page), "frame": frame_selector},
                "locator": {
                    "strategy": "css",
                    "value": redact_selector(locator.get("value", "")),
                    "fallbacks": [redact_selector(f) for f in (locator.get("fallbacks") or [])],
                },
                "inputs": {},
                "page_url_redacted": redact_url(page_url),
                "page_title": redact_text(_safe_title(page)),
                "selector": redact_selector(locator.get("value", "")),
                # Treat the field's type as the security boundary. Keyword
                # redaction cannot protect an arbitrary password value.
                "target_text_redacted": (
                    "[redacted]" if payload.get("credentialField") or payload.get("secret")
                    else redact_text(payload.get("text", ""))
                ),
                "x_ratio": payload.get("x"),
                "y_ratio": payload.get("y"),
                "before_image": before,
                "after_image": after,
            }
            if (
                payload.get("relativeToElement")
                and step["locator"].get("value") not in {"", "[redacted]"}
                and payload.get("elementX") is not None
                and payload.get("elementY") is not None
            ):
                step["locator"].update({
                    "position_mode": "element_relative",
                    "element_x_ratio": float(payload["elementX"]),
                    "element_y_ratio": float(payload["elementY"]),
                })
            self._fill_contract(step, payload)
            self._emit(
                step,
                quality_before=before_quality,
                quality_after=after_quality,
                url_after=url_after,
            )
        except Exception:
            pass  # a step we failed to record must not take down the recording

    def _fill_contract(self, step: dict[str, Any], payload: dict) -> None:
        """Inputs, success evidence and retry policy, decided per action type."""
        action = step["action"]

        if action == "fill":
            credential_field = payload.get("credentialField") or (
                "password" if payload.get("secret") else ""
            )
            if credential_field:
                # The reference names the system whose credential fills this
                # field at run time. The value itself is never written down.
                step["inputs"] = {
                    "secret_ref": "",  # resolved to the system key when the plan is built
                    "secret_field": credential_field,
                }
                step["success"] = {"type": "value_not_empty"}
            else:
                value = payload.get("value", "")
                step["inputs"] = {"value": value}
                step["success"] = {"type": "value_equals", "value": value}
            # Typing the same value again lands on the same state.
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "select":
            values = payload.get("values")
            if isinstance(values, list):
                chosen = [str(item) for item in values]
                step["inputs"] = {"values": chosen}
                # Reading back one value would pass while two of the three
                # choices were missing, so the whole set is what gets proved.
                step["success"] = {"type": "selected_values_are", "value": chosen}
            else:
                value = payload.get("value", "")
                step["inputs"] = {"value": value}
                step["success"] = {"type": "value_equals", "value": value}
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "check":
            checked = bool(payload.get("checked"))
            step["inputs"] = {"checked": checked}
            step["success"] = {"type": "checked_is", "value": checked}
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "press":
            step["inputs"] = {"key": payload.get("key", "")}
            self._attach_observed_locators(step, payload)
            # What a key press does is entirely page-specific, so its evidence is
            # filled in during review rather than guessed at here.
            step["success"] = {"type": "none"}
            # Enter usually submits. Repeating a submit can double-file a request.
            step["retry"] = {"max_attempts": 1, "safe_to_repeat": False}

        elif action == "drag":
            drop = payload.get("dropLocator") or {}
            step["inputs"] = {
                "drop_selector": redact_selector(drop.get("value", "")),
                "drop_fallbacks": [
                    redact_selector(item) for item in (drop.get("fallbacks") or [])
                ],
            }
            self._attach_observed_locators(step, payload)
            # Where something ended up is page-specific; review decides the check.
            step["success"] = {"type": "none"}
            # Dropping the same thing twice can reorder a list into a different
            # place than the person left it.
            step["retry"] = {"max_attempts": 1, "safe_to_repeat": False}

        elif action == "hover":
            step["inputs"] = {}
            self._attach_observed_locators(step, payload)
            # What a hover reveals is decided during review, from what appeared.
            step["success"] = {"type": "none"}
            # Resting the pointer somewhere changes nothing on its own, so it is
            # the one gesture that is always safe to repeat.
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        else:  # click, pointer_click or context_click
            step["inputs"] = {}
            self._attach_observed_locators(step, payload)
            step["success"] = {"type": "none"}
            step["retry"] = {"max_attempts": 1, "safe_to_repeat": False}

    def _observable_locators(self, scope: Any) -> list[dict[str, Any]]:
        try:
            observed = scope.evaluate(
                "() => window.__smartopsObservableLocators ? window.__smartopsObservableLocators() : []"
            )
        except Exception:
            return []
        return _redacted_observed_locators(observed)

    @staticmethod
    def _attach_observed_locators(step: dict[str, Any], payload: dict) -> None:
        """Keep only redacted selector facts needed to infer an observed effect."""
        before = _redacted_observed_locators(payload.get("observedVisibleLocators"))
        after = _redacted_observed_locators(payload.get("observedVisibleLocatorsAfter"))
        if before:
            step["inputs"]["_observed_visible_before"] = before
        if after:
            step["inputs"]["_observed_visible_after"] = after

    def _emit(
        self,
        step: dict[str, Any],
        *,
        quality_before: dict[str, Any] | None = None,
        quality_after: dict[str, Any] | None = None,
        url_after: str = "",
    ) -> None:
        step.setdefault("target", {"page": "main", "frame": ""})
        step.setdefault("locator", {})
        step.setdefault("inputs", {})
        step.setdefault("success", {"type": "none"})
        step.setdefault("retry", {"max_attempts": 1, "safe_to_repeat": False})
        # Built from the step dict about to be emitted, on the same thread, right
        # before it is handed to on_step: the timeline's sequence numbers and the
        # eventual RecordingStep's sequence numbers advance together only because
        # nothing can happen to one without the other in between.
        observation = self.timeline.record(
            step,
            quality_before=quality_before,
            quality_after=quality_after,
            url_after=url_after,
        )
        self._note_confidence(step, observation)
        self.on_step(step)

    def _note_confidence(self, step: dict[str, Any], observation: ActionObservation) -> None:
        """Score the step as it is captured, so a weak one can be flagged while recording.

        Scored here rather than only at review time, because the point of
        catching a weak step is being able to ask the person to redo it while
        the browser is still open — not forty minutes into a replay that never
        gets the chance.
        """
        confidence = score_step(step, observation)
        if not confidence.weak:
            return
        self._weak_steps.append({
            "seq": observation.seq,
            "action": step.get("action") or step.get("kind") or "",
            "reason": confidence.reason,
        })
        del self._weak_steps[:-20]  # a live signal for this recording, not a history to keep

    def _shoot(self, page: Any, *, deep: bool = False) -> str:
        """Best-effort screenshot; "" (never None) on failure, so callers treat it uniformly.

        The frame is measured on the way out. A blank one is still saved — a
        screen that showed nothing is a fact worth keeping — but it is written
        down as blank, so review shows "no picture available" instead of a grey
        square that looks like evidence.
        """
        capture = self.vision.capture(page, deep=deep)
        self._shot_seq = self.vision.frames_taken
        self._last_capture_quality = capture.quality.to_dict() if capture.quality else None
        return capture.relative_path


def _redacted_observed_locators(observed: Any) -> list[dict[str, Any]]:
    """Validate a browser snapshot before it reaches the recording contract."""
    if not isinstance(observed, list):
        return []
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in observed[:200]:
        if not isinstance(raw, dict):
            continue
        value = redact_selector(str(raw.get("value") or ""))
        if not value or value == "[redacted]" or value in seen:
            continue
        fallbacks = [
            redacted for item in list(raw.get("fallbacks") or [])[:8]
            if (redacted := redact_selector(str(item or ""))) not in {"", "[redacted]"}
        ]
        safe.append({"strategy": "css", "value": value, "fallbacks": fallbacks})
        seen.add(value)
    return safe


def _safe_url(page: Any) -> str:
    try:
        return page.url
    except Exception:
        return ""


def _safe_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:
        return ""
