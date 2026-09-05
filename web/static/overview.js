/* Overview: the journey, rendered from the server's own view of it.

   The old version of this page recomputed progress in the browser from three
   API calls and its own guesses. Now /api/journey is the single source of truth,
   so what this page shows, what the sidebar shows, and what the API will
   actually allow are the same thing by construction. */

const {
  getJSON, postJSON, formatDate, statusBadge, badge, RUN_STATUS_LABELS,
  EVENT_TYPE_LABELS, shortId, showError, connectEvents, el,
} = SmartOps;
const errorBox = document.getElementById("error");

function renderNextStep(journey) {
  const box = document.getElementById("next-step");
  box.innerHTML = "";
  const stage = journey.stages.find(s => s.key === journey.current);
  if (journey.complete || !stage) {
    box.appendChild(el("p", { class: "next-step-title", text: "Everything is set up and running." }, []));
    box.appendChild(el("p", { class: "muted", text: "Your automations run on their own. This page tells you if anything needs you." }, []));
    return;
  }
  box.appendChild(el("p", { class: "next-step-title" }, [`Step ${stage.number}: ${stage.title}`]));
  box.appendChild(el("p", { class: "muted", text: stage.purpose }, []));
  box.appendChild(el("p", { text: stage.detail }, []));
  if (stage.action) {
    box.appendChild(el("a", { class: "button-link", href: stage.action.href }, [stage.action.label]));
  }
}

function renderJourney(journey) {
  const list = document.getElementById("journey-steps");
  list.innerHTML = "";
  for (const stage of journey.stages) {
    const state = stage.done ? badge("Done", "green")
      : stage.key === journey.current ? badge("Do this now", "blue")
      : badge("Waiting", "gray");
    const row = el("li", { class: stage.blocked ? "journey-step blocked" : "journey-step" }, [
      el("span", { class: "journey-number" }, [String(stage.number)]),
      el("span", { class: "msg" }, [
        el("strong", {}, [stage.title]),
        el("div", { class: "muted hint" }, [stage.purpose]),
        el("div", { class: "hint" }, [stage.detail]),
      ]),
      el("span", { class: "time" }, [state]),
    ]);
    // Only the stage you can actually act on carries a link — offering eleven
    // equal buttons is what made this feel like a pile of pages before.
    if (stage.action && !stage.blocked) {
      row.querySelector(".msg").appendChild(
        el("a", { class: "link", href: stage.action.href }, [stage.action.label])
      );
    }
    list.appendChild(row);
  }
}

async function loadJourney() {
  try {
    const journey = await getJSON("/api/journey");
    renderNextStep(journey);
    renderJourney(journey);
  } catch (err) {
    showError(errorBox, err);
  }
}

async function loadDashboard() {
  try {
    const [runs, incidents, processes] = await Promise.all([
      getJSON("/api/runs?limit=8"),
      getJSON("/api/incidents?status=open&limit=100"),
      getJSON("/api/processes?limit=200"),
    ]);

    const active = runs.items.filter(r => ["queued", "running", "waiting", "retrying"].includes(r.status));
    document.getElementById("active-count").textContent = active.length;
    document.getElementById("open-incidents-count").textContent = incidents.items.length;
    document.getElementById("scheduled-count").textContent =
      processes.items.filter(p => p.is_scheduled).length;

    const tbody = document.getElementById("recent-runs");
    tbody.innerHTML = "";
    if (runs.items.length === 0) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["Nothing has run yet"])]));
    }
    for (const run of runs.items) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [describeRun(run)]),
        el("td", {}, [statusBadge(RUN_STATUS_LABELS, run.status)]),
        el("td", {}, [formatDate(run.started_at || run.created_at)]),
        el("td", {}, [el("a", { class: "link", href: `run.html?id=${encodeURIComponent(run.id)}` }, ["View"])]),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

/* A workflow key means nothing to the person reading this page; the system and
   report it worked on do. */
function describeRun(run) {
  const system = run.params?.system;
  const report = run.params?.report;
  if (system && report) return `${report} — ${system}`;
  if (run.workflow_key === "platform.selfcheck") return "Platform health check";
  return run.workflow_key;
}

document.getElementById("run-selfcheck").addEventListener("click", async (event) => {
  const button = event.target;
  button.disabled = true;
  document.getElementById("selfcheck-hint").textContent = "Running…";
  try {
    const run = await postJSON("/api/runs", { workflow: "platform.selfcheck", start: true });
    document.getElementById("selfcheck-hint").textContent =
      run.status === "succeeded" ? "The platform is healthy." : `Result: ${run.status}`;
    await loadDashboard();
  } catch (err) {
    showError(errorBox, err);
    document.getElementById("selfcheck-hint").textContent = "";
  } finally {
    button.disabled = false;
  }
});

const liveEvents = new Map();
let liveEventOrder = 0;

function renderLiveEvents() {
  const list = document.getElementById("live-events");
  list.innerHTML = "";
  const events = [...liveEvents.values()]
    .sort((a, b) => {
      const byDate = new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      return byDate || b._receivedOrder - a._receivedOrder;
    })
    .slice(0, 15);
  if (events.length === 0) {
    list.appendChild(el("li", { class: "empty" }, ["No activity yet"]));
    return;
  }
  for (const evt of events) {
    const label = EVENT_TYPE_LABELS[evt.type] || evt.type;
    list.appendChild(el("li", {}, [
      el("span", { class: "msg" }, [evt.message || label]),
      el("span", { class: "time" }, [formatDate(evt.created_at)]),
    ]));
  }
}

function addLiveEvent(evt) {
  const key = evt.id || `${evt.type}:${evt.run_id || ""}:${evt.created_at || ""}`;
  if (liveEvents.has(key)) return;
  liveEvents.set(key, { ...evt, _receivedOrder: liveEventOrder++ });
  renderLiveEvents();
}

async function loadRecentEvents() {
  try {
    const events = await getJSON("/api/events?limit=15");
    for (const evt of events.items) addLiveEvent(evt);
  } catch (err) {
    showError(errorBox, err);
  }
}

loadJourney();
loadDashboard();
loadRecentEvents();

const socket = connectEvents(null, (evt) => {
  document.getElementById("live-dot").classList.add("on");
  addLiveEvent(evt);
  // A finished run can complete a whole stage, so refresh the journey with it
  // rather than leaving the page showing a step the user has already passed.
  if (["run_succeeded", "run_failed", "process_approved", "process_tested", "file_validated"].includes(evt.type)) {
    loadJourney();
    loadDashboard();
  }
});
socket.onopen = () => document.getElementById("live-dot").classList.add("on");
socket.onclose = () => document.getElementById("live-dot").classList.remove("on");
