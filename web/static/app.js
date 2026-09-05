/* Shared UI utilities and the English SmartOps application shell. */

function mountAppShell() {
  const page = document.body.dataset.page || "overview";
  const title = document.body.dataset.title || "Overview";
  // Ordered by the actual dependency chain, because every step here is
  // useless until the one above it is done: you define a system, you sign
  // in to it, you teach it a workflow by recording one, you run it, you
  // read the files it produced, and you deal with incidents when a run
  // breaks. The previous order (Runs second, Credentials after Recordings)
  // put executions before the setup they depend on.
  const nav = [
    ["overview", "1 · Overview", "index.html", "⌂"],
    ["systems", "2 · Systems", "systems.html", "▤"],
    ["credentials", "3 · Sign-in", "credentials.html", "▣"],
    ["recordings", "4 · Recordings", "recordings.html", "●"],
    ["runs", "5 · Runs", "runs.html", "↗"],
    ["files", "6 · Files", "files.html", "□"],
    ["incidents", "7 · Incidents", "incidents.html", "!"],
  ];
  const links = nav.map(([key, label, href, icon]) =>
    `<a class="side-nav-link${key === page ? " active" : ""}" href="${href}"${key === page ? ' aria-current="page"' : ""}><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</a>`
  ).join("");
  const sidebar = document.createElement("aside");
  sidebar.className = "app-sidebar";
  sidebar.innerHTML = `<a class="brand" href="index.html" aria-label="SmartOps home"><span class="brand-mark">S</span><span><strong>SmartOps</strong><small>OPERATIONS OS</small></span></a><nav class="side-nav" aria-label="Primary navigation">${links}</nav><div class="sidebar-footer"><span class="status-dot" aria-hidden="true"></span><span>Local workspace</span></div>`;
  const main = document.querySelector("main");
  if (!main) return;
  const topbar = document.createElement("div");
  topbar.className = "app-topbar";
  topbar.innerHTML = `<div><p class="eyebrow">OPERATIONS CENTER</p><h1>${title}</h1></div><div class="topbar-actions"><span class="workspace-status"><span class="status-dot" aria-hidden="true"></span>System online</span><button class="menu-toggle" type="button" aria-label="Open navigation" aria-expanded="false">☰</button></div>`;
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
  // Turns a FastAPI error body into one readable string. `detail` shows up
  // in three different shapes depending on where the error came from: a
  // plain string (Starlette's own HTTPException default), our own
  // {message, error_class, ...} dict (SmartOpsError.to_dict()), or — this is
  // the one earlier code missed — an ARRAY of {msg, loc, type} objects,
  // which is what FastAPI sends automatically when request body validation
  // fails before our handler ever runs. Falling through to
  // `String(detail)` on that array silently produced "[object Object]".
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

  async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await errorMessage(res));
    return res.json();
  }

  async function postJSON(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) throw new Error(await errorMessage(res));
    try {
      return await res.json();
    } catch (_) {
      return null; // 2xx with no body
    }
  }

  const RUN_STATUS_LABELS = {
    queued: ["Queued", "gray"], running: ["Running", "blue"], waiting: ["Waiting", "yellow"],
    retrying: ["Retrying", "orange"], succeeded: ["Succeeded", "green"], failed: ["Failed", "red"], cancelled: ["Cancelled", "gray"],
  };

  const STEP_STATUS_LABELS = {
    pending: ["Pending", "gray"], running: ["Running", "blue"], waiting: ["Waiting", "yellow"],
    retrying: ["Retrying", "orange"], succeeded: ["Succeeded", "green"], failed: ["Failed", "red"], skipped: ["Skipped", "gray"],
  };

  const VALIDATION_STATUS_LABELS = {
    pending: ["Pending", "yellow"], passed: ["Valid", "green"], failed: ["Rejected", "red"],
  };

  const INCIDENT_STATUS_LABELS = {
    open: ["Open", "red"], diagnosing: ["Diagnosing", "orange"], fixing: ["Fixing", "yellow"],
    resolved: ["Resolved", "green"], escalated: ["Escalated", "blue"],
  };

  const SEVERITY_LABELS = {
    debug: ["Debug", "gray"], info: ["Info", "blue"], warning: ["Warning", "yellow"],
    error: ["Error", "red"], critical: ["Critical", "red"],
  };

  const EVENT_TYPE_LABELS = {
    run_created: "Run created", run_started: "Run started", run_waiting: "Run waiting", run_resumed: "Run resumed",
    run_succeeded: "Run completed", run_failed: "Run failed", run_cancelled: "Run cancelled",
    step_started: "Step started", step_succeeded: "Step completed", step_failed: "Step failed", step_retry_scheduled: "Retry scheduled",
    file_downloaded: "File downloaded", file_validated: "File validated", file_rejected: "File rejected", alert_raised: "Platform alert",
    incident_opened: "Incident opened", incident_closed: "Incident closed", agent_run_started: "AI agent started", agent_run_finished: "AI agent finished", escalated: "Escalated",
    recording_created: "Recording created", recording_started: "Browser recording started", recording_paused: "Recording paused", recording_resumed: "Recording resumed", recording_stopped: "Recording completed", recording_failed: "Recording failed", recording_deleted: "Recording moved to trash", recording_restored: "Recording restored", recording_draft_created: "Automation draft created",
  };

  const ERROR_CLASS_LABELS = {
    transient: "Temporary failure", rate_limit: "Rate limited", auth: "Session expired",
    target_not_found: "Target not found", data_quality: "File quality issue",
    permanent: "Configuration error", internal: "Internal platform error",
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
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
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
      else node.setAttribute(key, value);
    }
    for (const child of children || []) {
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function link(text, href) {
    return el("a", { class: "link", href }, [text]);
  }

  function showError(container, error) {
    container.innerHTML = "";
    // error is usually an Error (with a real .message string by now, thanks
    // to errorMessage above) but guard the other cases too, so a bug
    // elsewhere throwing a bare object/array never regresses to
    // "[object Object]" again.
    const text = error instanceof Error ? error.message
      : typeof error === "string" ? error
      : (() => { try { return JSON.stringify(error); } catch (_) { return String(error); } })();
    container.appendChild(el("div", { class: "error-box", text }));
  }

  function connectEvents(runId, onEvent) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const socket = new WebSocket(`${proto}://${location.host}/ws/events${query}`);
    socket.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data));
      } catch (_) {
        /* Unexpected message shape — ignore it. */
      }
    };
    return socket;
  }

  return {
    getJSON,
    postJSON,
    errorMessage,
    RUN_STATUS_LABELS,
    STEP_STATUS_LABELS,
    VALIDATION_STATUS_LABELS,
    INCIDENT_STATUS_LABELS,
    SEVERITY_LABELS,
    EVENT_TYPE_LABELS,
    ERROR_CLASS_LABELS,
    badge,
    statusBadge,
    formatDate,
    shortId,
    el,
    link,
    showError,
    connectEvents,
  };
})();
