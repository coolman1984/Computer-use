/* Overview: the ordered workflow with live state, plus recent activity.

   The point of the step list is that a new user should never have to guess
   what to do next. Each step reports done / not done from real data, and the
   first unfinished one carries the action. */
const { getJSON, postJSON, formatDate, statusBadge, badge, RUN_STATUS_LABELS, EVENT_TYPE_LABELS, shortId, link, showError, connectEvents, el } = SmartOps;
const errorBox = document.getElementById("error");

function stepRow(index, title, done, detail, action) {
  const children = [
    el("span", { class: "msg" }, [
      el("strong", {}, [`${index}. ${title}`]),
      el("div", { class: "muted hint" }, [detail]),
    ]),
    el("span", { class: "time" }, [done ? badge("Done", "green") : badge("To do", "yellow")]),
  ];
  if (action) children[0].appendChild(action);
  return el("li", {}, children);
}

async function loadWorkflowState() {
  const list = document.getElementById("workflow-steps");
  try {
    // Each step's "done" test is the cheapest honest signal available.
    const [systems, recordings, files] = await Promise.all([
      getJSON("/api/systems"),
      getJSON("/api/recordings?limit=1"),
      getJSON("/api/files?limit=1"),
    ]);

    const defined = systems.items.length;
    const needSignIn = systems.items.filter(s => s.auth_mode !== "none" && !s.session_exists);
    const connected = defined > 0 && needSignIn.length === 0;

    list.innerHTML = "";
    list.appendChild(stepRow(1, "Define a system",
      defined > 0,
      defined > 0
        ? `${defined} system${defined === 1 ? "" : "s"} loaded from your systems directory.`
        : "No systems found. Add a .yaml file to SMARTOPS_SYSTEMS_DIR and restart the server.",
      link("Open Systems", "systems.html")));

    list.appendChild(stepRow(2, "Sign in to each system",
      connected,
      defined === 0
        ? "Waiting on step 1."
        : connected
          ? "Every system that needs a sign-in has a saved session."
          : `Not signed in: ${needSignIn.map(s => s.key).join(", ")}. Run: python -m smartops login <system>`,
      link("Open Sign-in", "credentials.html")));

    list.appendChild(stepRow(3, "Record a workflow",
      recordings.items.length > 0,
      recordings.items.length > 0
        ? "At least one recording exists. Review its steps, then create an automation draft."
        : "Capture the clicks for a report once, in a real Chrome window, so it can be replayed later.",
      link("Open Recordings", "recordings.html")));

    list.appendChild(stepRow(4, "Collect a report",
      files.items.length > 0,
      files.items.length > 0
        ? "At least one file has been downloaded and validated."
        : "Use Collect now on a report to run it end to end for the first time.",
      link("Open Systems", "systems.html")));
  } catch (err) {
    showError(errorBox, err);
  }
}

async function loadDashboard() {
  try {
    const [runs, incidents] = await Promise.all([
      getJSON("/api/runs?limit=5"),
      getJSON("/api/incidents?status=open&limit=50"),
    ]);

    const active = runs.items.filter(r => ["queued", "running", "waiting", "retrying"].includes(r.status));
    document.getElementById("active-count").textContent = active.length;
    document.getElementById("open-incidents-count").textContent = incidents.items.length;

    const lastSelfcheck = runs.items.find(r => r.workflow_key === "platform.selfcheck");
    const statusEl = document.getElementById("selfcheck-status");
    statusEl.innerHTML = "";
    if (lastSelfcheck) statusEl.appendChild(statusBadge(RUN_STATUS_LABELS, lastSelfcheck.status));
    else statusEl.textContent = "Not run yet";

    const tbody = document.getElementById("recent-runs");
    tbody.innerHTML = "";
    if (runs.items.length === 0) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["No runs yet"])]));
    }
    for (const run of runs.items) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [run.workflow_key]),
        el("td", {}, [statusBadge(RUN_STATUS_LABELS, run.status)]),
        el("td", {}, [formatDate(run.started_at || run.created_at)]),
        el("td", {}, [link("View", `run.html?id=${encodeURIComponent(run.id)}`)]),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

document.getElementById("run-selfcheck").addEventListener("click", async (event) => {
  const button = event.target;
  button.disabled = true;
  document.getElementById("selfcheck-hint").textContent = "Running…";
  try {
    const run = await postJSON("/api/runs", { workflow: "platform.selfcheck", start: true });
    document.getElementById("selfcheck-hint").textContent =
      run.status === "succeeded" ? "Completed" : `Status: ${run.status}`;
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
    list.appendChild(el("li", { class: "empty" }, ["No events yet"]));
    return;
  }
  for (const evt of events) {
    const label = EVENT_TYPE_LABELS[evt.type] || evt.type;
    list.appendChild(el("li", {}, [
      el("span", { class: "msg" }, [`${label}${evt.run_id ? " — " + shortId(evt.run_id) : ""}`]),
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

loadWorkflowState();
loadDashboard();
loadRecentEvents();
const socket = connectEvents(null, (evt) => {
  document.getElementById("live-dot").classList.add("on");
  addLiveEvent(evt);
});
socket.onopen = () => document.getElementById("live-dot").classList.add("on");
socket.onclose = () => document.getElementById("live-dot").classList.remove("on");
