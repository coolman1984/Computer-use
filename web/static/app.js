/* Shared UI utilities and the SmartOps application shell.

   The navigation is the journey, in order, and it is not decorative: each entry
   shows whether its stage is done, and a stage whose prerequisites are not met
   is visibly held back rather than silently failing when clicked. The server
   computes all of that (/api/journey) so the sidebar and the API can never
   disagree about what is allowed. */

const NAV = [
  ["overview", "Overview", "index.html", "⌂", null],
  ["systems", "Systems & reports", "systems.html", "▤", "system"],
  ["credentials", "Sign-in", "credentials.html", "▣", "signin"],
  ["recordings", "Record a task", "recordings.html", "●", "recording"],
  ["processes", "Automations", "processes.html", "⚙", "approval"],
  ["runs", "Runs", "runs.html", "↗", "run"],
  ["files", "Results", "files.html", "□", "result"],
  ["incidents", "Issues", "incidents.html", "!", "monitor"],
];

function mountAppShell() {
  const page = document.body.dataset.page || "overview";
  const title = document.body.dataset.title || "Overview";
  const links = NAV.map(([key, label, href, icon, stage]) =>
    `<a class="side-nav-link${key === page ? " active" : ""}" href="${href}"${key === page ? ' aria-current="page"' : ""} data-stage="${stage || ""}">` +
    `<span class="nav-icon" aria-hidden="true">${icon}</span><span class="nav-label">${label}</span>` +
    `<span class="nav-state" aria-hidden="true"></span></a>`
  ).join("");
  const sidebar = document.createElement("aside");
  sidebar.className = "app-sidebar";
  sidebar.innerHTML =
    `<a class="brand" href="index.html" aria-label="SmartOps home"><span class="brand-mark">S</span><span><strong>SmartOps</strong><small>OPERATIONS OS</small></span></a>` +
    `<nav class="side-nav" aria-label="Primary navigation">${links}</nav>` +
    `<div class="sidebar-footer"><span class="status-dot" aria-hidden="true"></span><span id="runtime-status">Local workspace</span></div>`;
  const main = document.querySelector("main");
  if (!main) return;
  const topbar = document.createElement("div");
  topbar.className = "app-topbar";
  topbar.innerHTML =
    `<div><p class="eyebrow">OPERATIONS CENTER</p><h1>${title}</h1></div>` +
    `<div class="topbar-actions"><span class="workspace-status"><span class="status-dot" aria-hidden="true"></span><span id="worker-status">Checking…</span></span>` +
    `<button class="menu-toggle" type="button" aria-label="Open navigation" aria-expanded="false">☰</button></div>`;
  main.prepend(topbar);
  document.body.prepend(sidebar);
  document.querySelector("header.top")?.remove();
  const toggle = topbar.querySelector(".menu-toggle");
  toggle?.addEventListener("click", () => {
    const open = document.body.classList.toggle("sidebar-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
}

mountAppShell();

const SmartOps = (() => {
  // Turns a FastAPI error body into one readable string. `detail` shows up in
  // several shapes: a plain string, our own {message, guidance, ...} dict, or an
  // ARRAY of {msg, loc, type} objects when request-body validation fails before
  // our handler runs. Falling through to String(detail) on that array silently
  // produced "[object Object]".
  async function errorMessage(res) {
    let body = null;
    try { body = await res.json(); } catch (_) { /* body wasn't JSON */ }
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map(d => (d && typeof d.msg === "string") ? d.msg : JSON.stringify(d)).join("; ");
    }
    if (detail && typeof detail.message === "string") return detail.message;
    if (detail) return JSON.stringify(detail);
    return res.statusText || "Request failed.";
  }

  /* An error carrying the server's guidance: what happened, what to do, and the
     one button that fixes it. Thrown instead of a bare Error so every page can
     render a refusal the same helpful way without repeating the logic. */
  class ApiError extends Error {
    constructor(message, guidance, status) {
      super(message);
      this.guidance = guidance || null;
      this.status = status;
    }
  }

  async function toError(res) {
    let body = null;
    try { body = await res.clone().json(); } catch (_) { /* not JSON */ }
    const guidance = body?.detail?.guidance || null;
    return new ApiError(await errorMessage(res), guidance, res.status);
  }

  async function request(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) throw await toError(res);
    try { return await res.json(); } catch (_) { return null; }
  }

  const getJSON = (url) => request(url);
  const postJSON = (url, payload) => request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-SmartOps-Request": "web" },
    body: JSON.stringify(payload || {}),
  });
  const putJSON = (url, payload) => request(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-SmartOps-Request": "web" },
    body: JSON.stringify(payload || {}),
  });
  const patchJSON = (url, payload) => request(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-SmartOps-Request": "web" },
    body: JSON.stringify(payload || {}),
  });
  const deleteJSON = (url) => request(url, {
    method: "DELETE",
    headers: { "X-SmartOps-Request": "web" },
  });

  const RUN_STATUS_LABELS = {
    queued: ["Waiting to start", "gray"], running: ["Running", "blue"], waiting: ["Paused", "yellow"],
    retrying: ["Trying again", "orange"], succeeded: ["Succeeded", "green"], failed: ["Failed", "red"], cancelled: ["Cancelled", "gray"],
  };

  const STEP_STATUS_LABELS = {
    pending: ["Not started", "gray"], running: ["Running", "blue"], waiting: ["Paused", "yellow"],
    retrying: ["Trying again", "orange"], succeeded: ["Done", "green"], failed: ["Failed", "red"], skipped: ["Skipped", "gray"],
  };

  const VALIDATION_STATUS_LABELS = {
    pending: ["Not checked", "yellow"], passed: ["Valid", "green"], failed: ["Rejected", "red"],
  };

  const INCIDENT_STATUS_LABELS = {
    open: ["Needs attention", "red"], diagnosing: ["Being diagnosed", "orange"], fixing: ["Being fixed", "yellow"],
    resolved: ["Handled", "green"], escalated: ["Escalated", "blue"],
  };

  const SEVERITY_LABELS = {
    debug: ["Debug", "gray"], info: ["Info", "blue"], warning: ["Warning", "yellow"],
    error: ["Error", "red"], critical: ["Critical", "red"],
  };

  // An automation's stage, in the words the buttons use.
  const PROCESS_STATUS_LABELS = {
    draft: ["Not tested yet", "gray"], testing: ["Testing…", "blue"], tested: ["Test passed", "yellow"],
    test_failed: ["Test failed", "red"], approved: ["Approved", "green"], retired: ["Retired", "gray"],
  };

  const RECORDING_STATUS_LABELS = {
    draft: ["Not started", "gray"], starting: ["Opening browser", "blue"], recording: ["Recording", "red"],
    paused: ["Paused", "yellow"], stopping: ["Saving", "blue"], completed: ["Finished", "green"],
    failed: ["Failed", "red"], interrupted: ["Interrupted", "orange"],
  };

  const EVENT_TYPE_LABELS = {
    run_created: "Run created", run_started: "Run started", run_waiting: "Run paused", run_resumed: "Run resumed",
    run_succeeded: "Run completed", run_failed: "Run failed", run_cancelled: "Run cancelled",
    step_started: "Step started", step_succeeded: "Step completed", step_failed: "Step failed", step_retry_scheduled: "Trying again",
    file_downloaded: "File downloaded", file_validated: "File checked and valid", file_rejected: "File rejected", alert_raised: "Alert",
    incident_opened: "Issue opened", incident_closed: "Issue closed", agent_run_started: "AI assistant started",
    agent_run_finished: "AI assistant finished", escalated: "Escalated",
    recording_created: "Recording created", recording_started: "Recording started", recording_paused: "Recording paused",
    recording_resumed: "Recording resumed", recording_stopped: "Recording finished", recording_failed: "Recording failed",
    recording_deleted: "Recording moved to trash", recording_restored: "Recording restored",
    recording_draft_created: "Automation plan built",
    recording_sanitized: "Legacy credential field protected",
    process_created: "Automation created", process_test_started: "Automation test started",
    process_tested: "Automation passed its test", process_test_failed: "Automation test failed",
    process_approved: "Automation approved", process_retired: "Automation retired",
    process_schedule_changed: "Schedule changed",
    system_saved: "System saved", system_deleted: "System deleted",
    system_check_passed: "Connection test passed", system_check_failed: "Connection test failed",
    login_started: "Sign-in started", login_succeeded: "Signed in", login_failed: "Sign-in failed",
  };

  function badge(label, color) {
    const span = document.createElement("span");
    span.className = `badge badge-${color}`;
    span.textContent = label;
    return span;
  }

  function statusBadge(map, code) {
    const [label, color] = map[code] || [code, "gray"];
    return badge(label, color);
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("en-US", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function formatSize(bytes) {
    if (!bytes) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function shortId(id) {
    if (!id) return "—";
    return id.length > 14 ? `${id.slice(0, 10)}…` : id;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (value === true) node.setAttribute(key, "");
      else if (value !== false && value != null) node.setAttribute(key, value);
    }
    for (const child of children || []) {
      if (child == null || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function link(text, href) {
    return el("a", { class: "link", href }, [text]);
  }

  /* Renders a failure the way a non-technical user can act on it: one sentence
     of what happened, one of what to do, and a button that goes there. The raw
     technical message is kept but tucked away. */
  function showError(container, error) {
    container.innerHTML = "";
    const guidance = error?.guidance;
    const text = error instanceof Error ? error.message
      : typeof error === "string" ? error
      : (() => { try { return JSON.stringify(error); } catch (_) { return String(error); } })();

    if (!guidance) {
      container.appendChild(el("div", { class: "error-box", text }));
      return;
    }
    const box = el("div", { class: "error-box" }, [
      el("strong", { text: guidance.what_happened }, []),
      el("p", { class: "error-todo", text: guidance.what_to_do }, []),
    ]);
    if (guidance.action?.label) {
      box.appendChild(el("a", { class: "button-link", href: guidance.action.href }, [guidance.action.label]));
    }
    if (guidance.technical_detail && guidance.technical_detail !== guidance.what_happened) {
      const details = el("details", { class: "error-detail" }, [
        el("summary", { text: "Technical detail" }, []),
        el("p", { text: guidance.technical_detail }, []),
      ]);
      box.appendChild(details);
    }
    container.appendChild(box);
  }

  function clearError(container) {
    if (container) container.innerHTML = "";
  }

  /* A success line in the same slot as errors, so every action visibly resolves
     one way or the other rather than leaving the user guessing. */
  function showNotice(container, message, action) {
    container.innerHTML = "";
    const box = el("div", { class: "notice-box" }, [el("span", { text: message }, [])]);
    if (action?.label) {
      box.appendChild(el("a", { class: "button-link", href: action.href }, [action.label]));
    }
    container.appendChild(box);
  }

  function connectEvents(runId, onEvent) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const socket = new WebSocket(`${proto}://${location.host}/ws/events${query}`);
    socket.onmessage = (msg) => {
      try { onEvent(JSON.parse(msg.data)); } catch (_) { /* unexpected shape */ }
    };
    return socket;
  }

  /* Paint the sidebar from the server's view of the journey, and say plainly
     whether automatic runs are on — the one fact that tells a user the platform
     still works when they are not looking at it. */
  async function paintShell() {
    try {
      const [journey, health] = await Promise.all([
        getJSON("/api/journey"),
        getJSON("/health").catch(() => null),
      ]);
      const byStage = new Map(journey.stages.map(s => [s.key, s]));
      for (const anchor of document.querySelectorAll(".side-nav-link[data-stage]")) {
        const stage = byStage.get(anchor.dataset.stage);
        if (!stage) continue;
        const state = anchor.querySelector(".nav-state");
        state.textContent = stage.done ? "✓" : stage.blocked ? "·" : "→";
        anchor.classList.toggle("stage-done", stage.done);
        anchor.classList.toggle("stage-next", !stage.done && !stage.blocked);
        anchor.classList.toggle("stage-blocked", Boolean(stage.blocked));
        anchor.title = stage.blocked ? `Finish the earlier steps first — ${stage.purpose}` : stage.purpose;
      }
      const worker = document.getElementById("worker-status");
      if (worker && health) {
        worker.textContent = health.automatic_runs ? "Automatic runs: on" : "Automatic runs: off";
        worker.closest(".workspace-status")?.classList.toggle("status-warn", !health.automatic_runs);
      }
      return journey;
    } catch (_) {
      return null; // the shell is decoration; a page must still work without it
    }
  }

  paintShell();

  return {
    getJSON, postJSON, putJSON, patchJSON, deleteJSON, errorMessage, ApiError,
    RUN_STATUS_LABELS, STEP_STATUS_LABELS, VALIDATION_STATUS_LABELS, INCIDENT_STATUS_LABELS,
    SEVERITY_LABELS, EVENT_TYPE_LABELS, PROCESS_STATUS_LABELS, RECORDING_STATUS_LABELS,
    badge, statusBadge, formatDate, formatSize, shortId, el, link,
    showError, clearError, showNotice, connectEvents, paintShell,
  };
})();
