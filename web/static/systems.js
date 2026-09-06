/* Systems: steps 1 and 2 of the journey, both fully inside the app.

   Adding a system used to mean writing a YAML file by hand in a folder outside
   the repository and restarting the server — the first step of the journey lived
   entirely outside the product. This page writes the same file through the same
   validator and reloads it live. */

const {
  getJSON, putJSON, postJSON, deleteJSON, badge, showError, clearError, showNotice,
  el, paintShell,
} = SmartOps;

const errorBox = document.getElementById("error");
const form = document.getElementById("system-form");
const reportsBox = document.getElementById("reports");

/* ---------- the report sub-form ---------- */

function reportRow(report = {}) {
  const row = el("div", { class: "report-row" }, [
    el("div", { class: "field-row" }, [
      el("label", {}, ["Report name", el("input", { class: "r-title", value: report.title || "", placeholder: "Daily sales" }, [])]),
      el("label", {}, ["Report ID", el("input", { class: "r-key", required: true, value: report.key || "", placeholder: "daily_sales" }, [])]),
      el("label", {}, ["Report page", el("input", { class: "r-url", type: "url", required: true, value: report.url || "", placeholder: "https://intranet.example.com/reports/daily" }, [])]),
    ]),
    el("details", {
      class: "advanced-settings",
      open: Boolean(report.download_selector || report.direct_download_url || report.wait_selector),
    }, [
      el("summary", {}, ["Advanced direct-download settings (optional)"]),
      el("p", { class: "muted" }, ["Leave these blank when you will teach the task by recording it."]),
      el("div", { class: "field-row" }, [
        el("label", {}, ["Download button locator", el("input", { class: "r-download", value: report.download_selector || "", placeholder: "#export-csv" }, [])]),
        el("label", {}, ["Direct file address", el("input", { class: "r-direct", type: "url", value: report.direct_download_url || "", placeholder: "https://…/export.csv" }, [])]),
        el("label", {}, ["Wait until this appears", el("input", { class: "r-wait", value: report.wait_selector || "", placeholder: "#report-ready" }, [])]),
      ]),
    ]),
  ]);
  const remove = el("button", { type: "button", class: "secondary small" }, ["Remove this report"]);
  remove.addEventListener("click", () => {
    // Never leave zero rows: a system with no report cannot be saved, and an
    // empty form gives the user nothing to correct.
    if (reportsBox.children.length > 1) row.remove();
    else showNotice(errorBox, "A system needs at least one report.");
  });
  row.appendChild(remove);
  return row;
}

function readReports() {
  return [...reportsBox.querySelectorAll(".report-row")].map(row => {
    const value = (cls) => row.querySelector(cls).value.trim();
    const report = { key: value(".r-key"), title: value(".r-title") || value(".r-key"), url: value(".r-url") };
    if (value(".r-download")) report.download_selector = value(".r-download");
    if (value(".r-direct")) report.direct_download_url = value(".r-direct");
    if (value(".r-wait")) report.wait_selector = value(".r-wait");
    return report;
  });
}

/* ---------- the form ---------- */

let editingKey = null;
let keyWasEdited = false;

function systemId(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}

document.getElementById("name").addEventListener("input", (event) => {
  if (editingKey || keyWasEdited) return;
  document.getElementById("key").value = systemId(event.target.value);
});
document.getElementById("key").addEventListener("input", () => { keyWasEdited = true; });

function setAuthVisibility() {
  const mode = document.getElementById("auth-mode").value;
  document.getElementById("auth-fields").hidden = mode === "none";
  document.getElementById("unattended-fields").hidden = mode !== "unattended";
}

document.getElementById("auth-mode").addEventListener("change", setAuthVisibility);
document.getElementById("add-report").addEventListener("click", () => reportsBox.appendChild(reportRow()));

function resetForm() {
  editingKey = null;
  keyWasEdited = false;
  form.reset();
  document.getElementById("key").disabled = false;
  document.getElementById("form-title").textContent = "Register a system";
  form.querySelector("button[type=submit]").textContent = "Register system";
  document.getElementById("cancel-edit").hidden = true;
  document.getElementById("form-hint").textContent = "";
  reportsBox.innerHTML = "";
  reportsBox.appendChild(reportRow());
  setAuthVisibility();
}

function fillForm(system) {
  editingKey = system.key;
  keyWasEdited = true;
  document.getElementById("key").value = system.key;
  // The key names the folders files land in, so changing it would orphan
  // everything already collected. Editing creates a new system instead.
  document.getElementById("key").disabled = true;
  document.getElementById("name").value = system.name || "";
  document.getElementById("auth-mode").value = system.auth_mode || "none";
  document.getElementById("login-url").value = system.login_url || "";
  document.getElementById("logged-in-selector").value = system.logged_in_selector || "";
  document.getElementById("login-selector").value = system.login_selector || "";
  document.getElementById("username-selector").value = system.username_selector || "";
  document.getElementById("password-selector").value = system.password_selector || "";
  document.getElementById("submit-selector").value = system.submit_selector || "";
  document.getElementById("language-selector").value = system.language_selector || "";
  document.getElementById("popup-trigger-selector").value = system.popup_trigger_selector || "";
  document.getElementById("notice-close-selector").value = system.notice_close_selector || "";
  reportsBox.innerHTML = "";
  for (const report of system.reports.length ? system.reports : [{}]) reportsBox.appendChild(reportRow(report));
  setAuthVisibility();
  document.getElementById("form-title").textContent = `Edit ${system.name || system.key}`;
  document.getElementById("cancel-edit").hidden = false;
  form.querySelector("button[type=submit]").textContent = "Save changes";
  document.getElementById("form-title").scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("cancel-edit").addEventListener("click", resetForm);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(errorBox);
  const key = document.getElementById("key").value.trim() || editingKey;
  const mode = document.getElementById("auth-mode").value;
  const auth = { mode };
  if (mode !== "none") {
    auth.login_url = document.getElementById("login-url").value.trim();
    auth.logged_in_selector = document.getElementById("logged-in-selector").value.trim();
    auth.login_selector = document.getElementById("login-selector").value.trim();
  }
  if (mode === "unattended") {
    auth.credential_ref = key;
    auth.username_selector = document.getElementById("username-selector").value.trim();
    auth.password_selector = document.getElementById("password-selector").value.trim();
    auth.submit_selector = document.getElementById("submit-selector").value.trim();
    auth.language_selector = document.getElementById("language-selector").value.trim();
    auth.popup_trigger_selector = document.getElementById("popup-trigger-selector").value.trim();
    auth.notice_close_selector = document.getElementById("notice-close-selector").value.trim();
  }
  const payload = { key, name: document.getElementById("name").value.trim() || key, auth, reports: readReports() };

  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await putJSON(`/api/systems/${encodeURIComponent(key)}`, payload);
    showNotice(errorBox, `Saved. Next: test the connection to ${payload.name}.`);
    resetForm();
    await load();
    paintShell();
  } catch (err) {
    showError(errorBox, err);
  } finally {
    button.disabled = false;
  }
});

/* ---------- the list ---------- */

function signInCell(system) {
  if (system.auth_mode === "none") return el("td", { "data-label": "Sign-in" }, [badge("Not needed", "gray")]);
  if (system.auth_mode === "unattended") {
    return el("td", { "data-label": "Sign-in" }, [
      badge("Username & password", "blue"),
      el("div", { class: "muted hint" }, ["Save the password once on the Sign-in page."]),
    ]);
  }
  if (!system.session_exists) {
    const expired = system.session?.exists;
    return el("td", { "data-label": "Sign-in" }, [
      badge(expired ? "Sign-in expired" : "Not signed in", expired ? "orange" : "red"),
      el("div", { class: "muted hint" }, [
        expired
          ? "The saved sign-in no longer works. Sign in again from the Sign-in page."
          : "Sign in from the Sign-in page — one click, no terminal.",
      ]),
    ]);
  }
  const hours = system.session_age_hours != null ? system.session_age_hours.toFixed(1) : "?";
  return el("td", { "data-label": "Sign-in" }, [
    badge("Signed in", "green"),
    el("div", { class: "muted hint" }, [`Saved ${hours} hours ago.`]),
  ]);
}

function connectionCell(system, onTested) {
  const cell = el("td", { "data-label": "Connection" }, []);
  const check = system.connection_check;
  cell.appendChild(check ? badge("Tested", "green") : badge("Not tested", "yellow"));
  if (check?.summary) cell.appendChild(el("div", { class: "muted hint" }, [check.summary]));

  const button = el("button", { type: "button", class: "secondary small" }, [check ? "Test again" : "Test connection"]);
  button.addEventListener("click", async () => {
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Opening the site…";
    clearError(errorBox);
    try {
      const result = await postJSON(`/api/systems/${encodeURIComponent(system.key)}/check`, {});
      // The verdict always carries the next step, so a failed test is still a
      // usable answer rather than a dead end.
      showNotice(errorBox, `${result.summary} ${result.next_step || ""}`.trim());
      await onTested();
      paintShell();
    } catch (err) {
      showError(errorBox, err);
      button.textContent = original;
      button.disabled = false;
    }
  });
  cell.appendChild(button);
  return cell;
}

function actionsCell(system) {
  const edit = el("button", { type: "button", class: "secondary small" }, ["Edit"]);
  edit.addEventListener("click", () => fillForm(system));
  const remove = el("button", { type: "button", class: "danger small" }, ["Delete"]);
  remove.addEventListener("click", async () => {
    if (!confirm(`Delete "${system.name || system.key}"? Its recordings and collected files stay on disk.`)) return;
    clearError(errorBox);
    try {
      await deleteJSON(`/api/systems/${encodeURIComponent(system.key)}`);
      showNotice(errorBox, "System deleted.");
      await load();
      paintShell();
    } catch (err) {
      showError(errorBox, err);
    }
  });
  return el("td", { class: "actions", "data-label": "Actions" }, [edit, remove]);
}

async function load() {
  const body = document.getElementById("systems-body");
  try {
    const data = await getJSON("/api/systems");
    // Showing the folder the server actually read makes the usual setup mistake
    // self-diagnosing rather than a guess.
    if (data.directory) document.getElementById("systems-dir").textContent = data.directory;
    body.innerHTML = "";
    if (!data.items.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "5", class: "empty" }, [
        "No systems yet. Add your first one in the form below.",
      ])]));
      return;
    }
    for (const system of data.items) {
      body.appendChild(el("tr", {}, [
        el("td", { "data-label": "System" }, [
          el("strong", {}, [system.name || system.key]),
          el("div", { class: "muted hint" }, [`${system.reports.length} report(s)`]),
        ]),
        signInCell(system),
        connectionCell(system, load),
        el("td", { "data-label": "Reports" }, [
          el("ul", { class: "plain-list" }, system.reports.map(r => el("li", {}, [r.title || r.key]))),
        ]),
        actionsCell(system),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

resetForm();
load();
