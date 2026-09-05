/* The recordings list: step 3's entry point.

   Only systems that are actually ready to record against are offered. The server
   refuses the rest anyway (connection test, then sign-in), but showing the
   reason here means the user learns what is missing before clicking, not after. */

const {
  getJSON, postJSON, statusBadge, formatDate, showError, clearError, el, link,
  RECORDING_STATUS_LABELS,
} = SmartOps;

const errorBox = document.getElementById("error");

async function load() {
  const body = document.getElementById("rows");
  try {
    const includeDeleted = document.getElementById("deleted").checked;
    const data = await getJSON(`/api/recordings?include_deleted=${includeDeleted}`);
    body.innerHTML = "";
    if (!data.items.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "empty" }, [
        "Nothing recorded yet. Start with the form above.",
      ])]));
      return;
    }
    for (const record of data.items) {
      let action;
      if (record.deleted_at) {
        action = el("button", { type: "button", class: "secondary small" }, ["Restore"]);
        action.addEventListener("click", async () => {
          try {
            await postJSON(`/api/recordings/${record.id}/restore`, {});
            load();
          } catch (err) { showError(errorBox, err); }
        });
      } else {
        action = link("Open", `recording.html?id=${encodeURIComponent(record.id)}`);
      }
      body.appendChild(el("tr", {}, [
        el("td", {}, [el("strong", {}, [record.name]), el("div", { class: "muted hint" }, [`Version ${record.version}`])]),
        el("td", {}, [record.system_key]),
        el("td", {}, [statusBadge(RECORDING_STATUS_LABELS, record.status)]),
        el("td", {}, [String(record.step_count)]),
        el("td", {}, [formatDate(record.created_at)]),
        el("td", {}, [action]),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

document.getElementById("deleted").addEventListener("change", load);

document.getElementById("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(errorBox);
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const record = await postJSON("/api/recordings", {
      name: document.getElementById("name").value,
      system_key: document.getElementById("system").value,
    });
    location.href = `recording.html?id=${encodeURIComponent(record.id)}`;
  } catch (err) {
    // A 409 here is the journey talking: it says which earlier step is missing
    // and links straight to the page that fixes it.
    showError(errorBox, err);
    button.disabled = false;
  }
});

async function loadSystems() {
  const note = document.getElementById("system-note");
  const select = document.getElementById("system");
  const submit = document.querySelector("#create-form button[type=submit]");
  try {
    const data = await getJSON("/api/systems");
    const ready = data.items.filter(s =>
      s.connection_checked && (s.auth_mode === "none" || s.session_exists || s.auth_mode === "unattended")
    );
    for (const system of ready) {
      select.appendChild(el("option", { value: system.key }, [`${system.name || system.key}`]));
    }
    if (!data.items.length) {
      note.textContent = "No systems yet. Add one first on the Systems page.";
    } else if (!ready.length) {
      const pending = data.items.filter(s => !s.connection_checked).map(s => s.name || s.key);
      note.textContent = pending.length
        ? `Test the connection to ${pending.join(", ")} first, then sign in — recording needs both.`
        : "Sign in to your systems first; a recording of a login page is not useful.";
    } else {
      note.textContent = "";
    }
    const blocked = !ready.length;
    select.disabled = blocked;
    submit.disabled = blocked;
  } catch (err) {
    showError(errorBox, err);
  }
}

loadSystems();
load();
