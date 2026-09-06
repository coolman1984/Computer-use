/* One recording: capture (step 3), review (step 4), and promotion to an
   automation (step 5).

   The review section is the gate that used to be missing entirely. Previously
   "create draft" produced a JSON blob nobody could act on; now the plan is shown
   as sentences, judged, and — only if it is fit — offered as an automation. */

const {
  getJSON, postJSON, patchJSON, statusBadge, badge, formatDate, showError, clearError, showNotice,
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

const PROOFS = [
  ["selector_visible", "An element appears"],
  ["selector_hidden", "An element disappears"],
  ["value_equals", "The field has this value"],
  ["value_not_empty", "The secure field is filled"],
  ["checked_is", "The checkbox has this state"],
  ["url_changed", "The page address changes"],
  ["new_page", "A new tab opens"],
  ["page_available", "The recorded tab is available"],
  ["download_started", "A file starts downloading"],
];

function fact(label, value) {
  return el("div", { class: "review-fact" }, [
    el("span", { class: "muted hint" }, [label]),
    el("strong", {}, [String(value || "—")]),
  ]);
}

function actionEditor(action) {
  const target = action.target || {};
  const locator = action.locator || {};
  const inputs = action.inputs || {};
  const success = action.success || { type: "none" };
  const retry = action.retry || { max_attempts: 1, safe_to_repeat: false };
  const card = el("article", { class: "review-action" }, []);
  card.appendChild(el("div", { class: "issue-head" }, [
    el("strong", {}, [`Step ${action.seq} · ${action.action || action.kind}`]),
    badge(success.type === "none" ? "Needs proof" : "Proven",
      success.type === "none" ? "red" : "green"),
  ]));
  card.appendChild(el("div", { class: "review-facts" }, [
    fact("Page", action.page_title || "title not captured"),
    fact("Tab", target.page || "main"),
    fact("Frame", target.frame || "main page"),
    fact("Address", action.url || "not captured"),
    fact("Locator strategy", locator.strategy || (locator.value ? "css" : "none")),
    fact("Checkpoint", action.checkpoint || "none"),
  ]));

  const form = el("form", { class: "review-edit stack-form" }, []);
  const selectors = [locator.value, ...(locator.fallbacks || [])].filter(Boolean).join("\n");
  const locatorBox = el("textarea", { class: "edit-locators", rows: "3" }, []);
  locatorBox.value = selectors;
  form.appendChild(el("label", {}, [
    "Ways to find the element, best first",
    locatorBox,
    el("span", { class: "muted hint" }, ["One selector per line. The first match is used."]),
  ]));

  if (inputs.secret_ref) {
    form.appendChild(el("div", { class: "notice-box" }, [
      "Input: a saved credential is used securely. Its value is never shown or stored here.",
    ]));
  } else if (["fill", "select", "press", "navigate", "wait_for", "check"].includes(action.action)) {
    const key = action.action === "press" ? "key" : action.action === "navigate" ? "url" :
      action.action === "wait_for" ? "seconds" : action.action === "check" ? "checked" : "value";
    const input = el("input", { class: "edit-input", type: action.action === "check" ? "checkbox" : "text" }, []);
    if (action.action === "check") input.checked = Boolean(inputs.checked);
    else input.value = inputs[key] ?? "";
    input.dataset.key = key;
    form.appendChild(el("label", {}, ["Non-sensitive input", input]));
  }

  const proofSelect = el("select", { class: "edit-proof" }, []);
  proofSelect.appendChild(el("option", { value: "none" }, ["Choose proof of success"]));
  for (const [value, label] of PROOFS) {
    const option = el("option", { value }, [label]);
    option.selected = success.type === value;
    proofSelect.appendChild(option);
  }
  const proofValue = el("input", { class: "edit-proof-value", value: success.value ?? "", placeholder: "Expected selector, value, or address" }, []);
  const timeout = el("input", { class: "edit-timeout", type: "number", min: "1", max: "300", value: action.wait_timeout_seconds || 15 }, []);
  const attempts = el("input", { class: "edit-attempts", type: "number", min: "1", max: "5", value: retry.max_attempts || 1 }, []);
  const safe = el("input", { class: "edit-safe", type: "checkbox" }, []);
  safe.checked = Boolean(retry.safe_to_repeat);
  safe.disabled = !retry.safe_to_repeat;
  form.appendChild(el("div", { class: "field-row" }, [
    el("label", {}, ["Proof of success", proofSelect]),
    el("label", {}, ["Proof value", proofValue]),
    el("label", {}, ["Wait up to (seconds)", timeout]),
  ]));
  form.appendChild(el("div", { class: "field-row" }, [
    el("label", {}, ["Maximum attempts", attempts]),
    el("label", { class: "check-label" }, [safe, " Safe to repeat"]),
    fact("Recorded retry policy", retry.safe_to_repeat ? "repeatable" : "one attempt only"),
  ]));

  const save = el("button", { type: "submit" }, ["Save and re-check this step"]);
  form.appendChild(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError(errorBox);
    save.disabled = true;
    try {
      const candidates = locatorBox.value.split("\n").map(v => v.trim()).filter(Boolean);
      const proof = { type: proofSelect.value };
      if (["selector_visible", "selector_hidden", "value_equals", "url_changed"].includes(proof.type)) {
        proof.value = proofValue.value;
      }
      const input = form.querySelector(".edit-input");
      if (proof.type === "checked_is") proof.value = Boolean(input?.checked);
      const payload = {
        locator_candidates: candidates,
        success: proof,
        wait_timeout_seconds: Number(timeout.value),
        retry: { max_attempts: Number(attempts.value), safe_to_repeat: safe.checked },
      };
      if (input) {
        payload.inputs = { [input.dataset.key]: input.type === "checkbox" ? input.checked :
          input.dataset.key === "seconds" ? Number(input.value) : input.value };
      }
      await patchJSON(`/api/recordings/${id}/draft/actions/${action.seq}`, payload);
      showNotice(errorBox, `Step ${action.seq} saved and checked again.`);
      await load();
    } catch (err) {
      showError(errorBox, err);
      save.disabled = false;
    }
  });
  card.appendChild(form);
  return card;
}

function renderReview(review, planSummary, actions) {
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

  const editors = document.getElementById("action-editors");
  editors.innerHTML = "";
  for (const action of actions || []) editors.appendChild(actionEditor(action));

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

    renderReview(
      data.review,
      data.plan_summary,
      data.recording.automation_draft?.actions || [],
    );
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
