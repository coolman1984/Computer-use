/* One automation: the gates, in order, with exactly one thing to do at a time.

   The "What to do next" panel is the whole design of this page. Rather than
   showing five buttons and letting the server reject four of them, it offers the
   single action this automation's current stage allows, and says why. */

const {
  getJSON, postJSON, putJSON, statusBadge, badge, formatDate, formatSize,
  showError, clearError, showNotice, connectEvents, el, link,
  PROCESS_STATUS_LABELS, VALIDATION_STATUS_LABELS, paintShell,
} = SmartOps;

const id = new URLSearchParams(location.search).get("id");
const errorBox = document.getElementById("error");
let current = null;

async function act(url, button, busyLabel, onDone) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = busyLabel;
  clearError(errorBox);
  try {
    const result = await postJSON(url, {});
    await onDone(result);
    paintShell();
  } catch (err) {
    showError(errorBox, err);
    button.textContent = original;
    button.disabled = false;
  }
}

function renderNextAction(data) {
  const process = data.process;
  const box = document.getElementById("next-action");
  box.innerHTML = "";

  const say = (text) => box.appendChild(el("p", { text }, []));
  const muted = (text) => box.appendChild(el("p", { class: "muted", text }, []));

  if (process.status === "retired") {
    say("This automation is retired and will not run again.");
    muted("Create a new one from its recording if you need it back.");
    return;
  }

  if (process.status === "testing") {
    say("A test run is in progress.");
    muted("It is running against the real system right now. This page updates itself when it finishes.");
    if (process.last_test_run_id) {
      box.appendChild(link("Watch the test run", `run.html?id=${encodeURIComponent(process.last_test_run_id)}`));
    }
    return;
  }

  if (["draft", "test_failed"].includes(process.status)) {
    if (process.status === "test_failed") {
      say("The last test failed, so this automation was not approved.");
      // The failure already carries what-happened / what-to-do from the server.
      if (data.guidance) {
        box.appendChild(el("div", { class: "error-box" }, [
          el("strong", { text: data.guidance.what_happened }, []),
          el("p", { text: data.guidance.what_to_do }, []),
          el("a", { class: "button-link", href: data.guidance.action.href }, [data.guidance.action.label]),
        ]));
      }
    } else {
      say("Test it before anything else.");
      muted("It runs once against the real system and must produce a valid file. Nothing can be approved or scheduled until that happens.");
    }
    const test = el("button", { type: "button" }, ["Run the test now"]);
    test.addEventListener("click", () => act(
      `/api/processes/${id}/test`, test, "Testing — this may take a few minutes…",
      async (result) => {
        showNotice(errorBox, result.process.status === "tested"
          ? "The test passed. You can approve this automation now."
          : "The test did not pass. Open the run to see which step stopped it.");
        await load();
      },
    ));
    box.appendChild(test);
    if (process.last_test_run_id) {
      box.appendChild(link("See the last test run", `run.html?id=${encodeURIComponent(process.last_test_run_id)}`));
    }
    return;
  }

  if (process.status === "tested") {
    say("The test passed. Approve it to let it run on its own.");
    muted("Approving is your decision that this is safe to run unattended. After that you can run it any time and put it on a schedule.");
    const approve = el("button", { type: "button" }, ["Approve this automation"]);
    approve.addEventListener("click", () => act(
      `/api/processes/${id}/approve`, approve, "Approving…",
      async () => {
        showNotice(errorBox, "Approved. You can run it now, or set a schedule below.");
        await load();
      },
    ));
    const retest = el("button", { type: "button", class: "secondary" }, ["Test again"]);
    retest.addEventListener("click", () => act(
      `/api/processes/${id}/test`, retest, "Testing…", async () => { await load(); },
    ));
    box.append(approve, retest);
    return;
  }

  // Approved.
  say("Approved and ready.");
  muted(process.is_scheduled
    ? "It runs on its own on the schedule below. You can also run it now."
    : "Run it now, or set a schedule below so it happens without you.");
  const run = el("button", { type: "button" }, ["Run it now"]);
  run.addEventListener("click", () => act(
    `/api/processes/${id}/run`, run, "Starting…",
    async (result) => { location.href = `run.html?id=${encodeURIComponent(result.id)}`; },
  ));
  const retire = el("button", { type: "button", class: "danger" }, ["Retire it"]);
  retire.addEventListener("click", async () => {
    if (!confirm("Retire this automation? It will stop running, including on its schedule.")) return;
    await act(`/api/processes/${id}/retire`, retire, "Retiring…", async () => { await load(); });
  });
  box.append(run, retire);
}

function renderInfo(data) {
  const process = data.process;
  const info = document.getElementById("info");
  info.innerHTML = "";
  info.append(
    el("h2", {}, [process.name]),
    statusBadge(PROCESS_STATUS_LABELS, process.status),
    el("p", { class: "muted" }, [
      `${process.system_key} · report "${process.report_key}" · ${process.action_count} step(s) · version ${process.version}`,
    ]),
  );
  if (process.approved_at) {
    info.appendChild(el("p", { class: "muted hint" }, [`Approved ${formatDate(process.approved_at)}`]));
  }
  if (data.last_run) {
    info.appendChild(el("p", { class: "muted hint" }, [
      "Last run: ", formatDate(data.last_run.created_at), " — ",
      link("open it", `run.html?id=${encodeURIComponent(data.last_run.id)}`),
    ]));
  }

  const plan = document.getElementById("plan");
  plan.innerHTML = "";
  if (!data.plan_summary?.length) plan.appendChild(el("li", { class: "empty" }, ["No steps"]));
  data.plan_summary.forEach((line, index) => {
    plan.appendChild(el("li", {}, [
      el("span", { class: "journey-number" }, [String(index + 1)]),
      el("span", { class: "msg" }, [line]),
    ]));
  });
}

function renderFiles(files) {
  const body = document.getElementById("files");
  body.innerHTML = "";
  if (!files.length) {
    body.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "empty" }, ["Nothing yet"])]));
    return;
  }
  for (const file of files) {
    body.appendChild(el("tr", {}, [
      el("td", {}, [file.original_name || file.path]),
      el("td", {}, [statusBadge(VALIDATION_STATUS_LABELS, file.validation_status)]),
      el("td", {}, [formatSize(file.size_bytes)]),
      el("td", {}, [file.row_count == null ? "—" : String(file.row_count)]),
      el("td", {}, [formatDate(file.created_at)]),
      el("td", {}, [el("a", { class: "link", href: `/api/files/${encodeURIComponent(file.id)}/download` }, ["Download"])]),
    ]));
  }
}

/* ---------- the schedule form ---------- */

const scheduleForm = document.getElementById("schedule-form");
const kind = document.getElementById("schedule-kind");

function syncScheduleFields() {
  document.getElementById("daily-field").hidden = kind.value !== "daily";
  document.getElementById("interval-field").hidden = kind.value !== "interval";
}
kind.addEventListener("change", syncScheduleFields);

function fillSchedule(process) {
  const schedule = process.schedule;
  if (!process.schedule_enabled && !schedule.enabled) kind.value = "off";
  else if (schedule.daily_at) { kind.value = "daily"; document.getElementById("daily-at").value = schedule.daily_at; }
  else if (schedule.every_seconds) { kind.value = "interval"; document.getElementById("every-minutes").value = Math.round(schedule.every_seconds / 60); }
  else kind.value = "off";
  syncScheduleFields();

  // Scheduling is gated on approval, so the form says why rather than failing
  // on submit.
  const blocked = !process.is_runnable;
  scheduleForm.querySelector("button[type=submit]").disabled = blocked;
  kind.disabled = blocked;
  document.getElementById("schedule-hint").textContent = blocked
    ? "Approve this automation first — only something proven to work may run unattended."
    : process.is_scheduled ? "Running automatically." : "";
}

scheduleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(errorBox);
  const payload = { enabled: kind.value !== "off", daily_at: "", every_seconds: null };
  if (kind.value === "daily") payload.daily_at = document.getElementById("daily-at").value;
  if (kind.value === "interval") payload.every_seconds = Number(document.getElementById("every-minutes").value) * 60;
  const button = scheduleForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await putJSON(`/api/processes/${id}/schedule`, payload);
    showNotice(errorBox, payload.enabled
      ? "Saved. This automation now runs on its own while SmartOps is open."
      : "Automatic runs turned off.");
    await load();
    paintShell();
  } catch (err) {
    showError(errorBox, err);
    button.disabled = false;
  }
});

async function load() {
  try {
    const data = await getJSON(`/api/processes/${id}`);
    current = data.process;
    renderInfo(data);
    renderNextAction(data);
    renderFiles(data.files || []);
    fillSchedule(data.process);
  } catch (err) {
    showError(errorBox, err);
  }
}

if (!id) {
  showError(errorBox, new Error("No automation selected. Go back and choose one."));
} else {
  load();
  connectEvents(null, (evt) => {
    // A test finishing on the background worker changes this page's whole
    // state, so react to its event rather than making the user refresh.
    if (evt.payload?.process_id === id || (current && evt.run_id === current.last_test_run_id)) load();
  });
}
