/* One recording: capture (step 3), review (step 4), and promotion to an
   automation (step 5).

   The review section is the gate that used to be missing entirely. Previously
   "create draft" produced a JSON blob nobody could act on; now the plan is shown
   as sentences, judged, and — only if it is fit — offered as an automation. */

const {
  getJSON, postJSON, statusBadge, formatDate, showError, clearError, showNotice,
  connectEvents, el, RECORDING_STATUS_LABELS, paintShell,
} = SmartOps;

const id = new URLSearchParams(location.search).get("id");
const errorBox = document.getElementById("error");
let current = null;

function controlButton(label, action, enabled, primary = false) {
  const button = el("button", { type: "button", class: primary ? "" : "secondary" }, [label]);
  button.disabled = !enabled;
  button.addEventListener("click", async () => {
    button.disabled = true;
    clearError(errorBox);
    try {
      await postJSON(`/api/recordings/${id}/${action}`, {});
      await load();
    } catch (err) {
      showError(errorBox, err);
      button.disabled = false;
    }
  });
  return button;
}

function renderReview(review, planSummary) {
  const box = document.getElementById("review");
  box.innerHTML = "";
  if (!review) {
    box.appendChild(el("p", { class: "muted", text: "Finish the recording, then build the plan to see this." }, []));
    return;
  }
  if (review.ready) {
    box.appendChild(el("div", { class: "notice-box" }, [
      el("span", {}, [`This recording can be repeated: ${review.action_count} step(s) captured cleanly.`]),
    ]));
  } else {
    const problems = el("div", { class: "error-box" }, [
      el("strong", {}, ["This recording cannot be repeated as it is."]),
    ]);
    for (const problem of review.problems) problems.appendChild(el("p", { text: problem }, []));
    box.appendChild(problems);
  }
  for (const warning of review.warnings || []) {
    box.appendChild(el("div", { class: "warn-box" }, [el("span", { text: warning }, [])]));
  }

  const list = document.getElementById("plan");
  list.innerHTML = "";
  if (!planSummary?.length) {
    list.appendChild(el("li", { class: "empty" }, ["No plan built yet"]));
    return;
  }
  planSummary.forEach((line, index) => {
    list.appendChild(el("li", {}, [
      el("span", { class: "journey-number" }, [String(index + 1)]),
      el("span", { class: "msg" }, [line]),
    ]));
  });
}

function renderPromote(review, processes) {
  const box = document.getElementById("promote");
  box.innerHTML = "";

  if (processes?.length) {
    box.appendChild(el("p", {}, ["This recording is already an automation:"]));
    for (const process of processes) {
      box.appendChild(el("p", {}, [
        el("a", { class: "link", href: `process.html?id=${encodeURIComponent(process.id)}` },
          [`${process.name} (version ${process.version})`]),
      ]));
    }
  }
  if (!review?.ready) {
    box.appendChild(el("p", { class: "muted", text: "Available once the plan above is complete." }, []));
    return;
  }

  const name = el("input", { maxlength: "200", placeholder: "Name for this automation", value: current?.name || "" }, []);
  const reportKey = el("input", { maxlength: "120", placeholder: "Short name for the report (optional)" }, []);
  const create = el("button", { type: "button" }, ["Create the automation"]);
  create.addEventListener("click", async () => {
    create.disabled = true;
    clearError(errorBox);
    try {
      const process = await postJSON("/api/processes", {
        recording_id: id,
        name: name.value,
        report_key: reportKey.value,
      });
      paintShell();
      location.href = `process.html?id=${encodeURIComponent(process.id)}`;
    } catch (err) {
      showError(errorBox, err);
      create.disabled = false;
    }
  });
  box.appendChild(el("div", { class: "toolbar" }, [name, reportKey, create]));
}

async function buildPlan() {
  clearError(errorBox);
  try {
    const result = await postJSON(`/api/recordings/${id}/draft`, {});
    showNotice(errorBox, result.review?.ready
      ? "Plan built. Review it below, then turn it into an automation."
      : "Plan built, but it has gaps — see the review below.");
    await load();
  } catch (err) {
    showError(errorBox, err);
  }
}

async function load() {
  try {
    const data = await getJSON(`/api/recordings/${id}`);
    current = data.recording;
    const status = current.status;

    const info = document.getElementById("info");
    info.innerHTML = "";
    info.append(
      el("h2", {}, [`${current.name} — version ${current.version}`]),
      statusBadge(RECORDING_STATUS_LABELS, status),
      el("p", { class: "muted" }, [
        `${current.system_key} · ${current.step_count} step(s) · ${current.download_count} file(s) downloaded · ${formatDate(current.started_at || current.created_at)}`,
      ]),
    );
    if (current.error_message) {
      info.appendChild(el("p", { class: "muted hint" }, [current.error_message]));
    }

    const controls = document.getElementById("controls");
    controls.innerHTML = "";
    const recording = ["recording", "paused"].includes(status);
    controls.append(
      controlButton("Start recording", "start", ["draft", "failed", "interrupted"].includes(status), true),
      controlButton("Pause", "pause", status === "recording"),
      controlButton("Continue", "resume", status === "paused"),
      controlButton("Finish and save", "stop", recording, true),
      controlButton("Record again", "rerecord", ["completed", "failed", "interrupted"].includes(status)),
      controlButton("Delete", "delete", !["recording", "paused", "starting", "stopping"].includes(status)),
    );
    if (status === "completed") {
      const build = el("button", { type: "button" }, [
        current.automation_draft?.actions ? "Rebuild the plan" : "Build the automation plan",
      ]);
      build.addEventListener("click", buildPlan);
      controls.appendChild(build);
    }

    const steps = document.getElementById("steps");
    steps.innerHTML = "";
    if (!data.steps.length) steps.appendChild(el("li", { class: "empty" }, ["Nothing captured yet"]));
    for (const step of data.steps) {
      steps.appendChild(el("li", {}, [
        el("span", { class: "msg" }, [`${step.kind} — ${step.selector || step.target_text_redacted || "click by position"}`]),
        el("span", { class: "time" }, [formatDate(step.occurred_at)]),
      ]));
    }

    renderReview(data.review, data.plan_summary);
    renderPromote(data.review, data.processes);
  } catch (err) {
    showError(errorBox, err);
  }
}

if (!id) {
  showError(errorBox, new Error("No recording selected. Go back and choose one."));
} else {
  load();
  connectEvents(null, (evt) => {
    if (evt.payload?.recording_id === id) load();
  });
  // A recording is driven by a human in another window, so poll as a backstop
  // in case an event is missed while that window has focus.
  setInterval(load, 5000);
}
