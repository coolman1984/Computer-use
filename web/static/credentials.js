/* Sign-in: step 3, entirely inside the app.

   This page used to tell the user to open a terminal and run
   `python -m smartops login <system>`. That single instruction broke the whole
   promise of the product for a non-technical user. Now the server opens the
   browser window, this page polls the attempt, and the user confirms with a
   button when they are done. */

const {
  getJSON, postJSON, deleteJSON, badge, showError, clearError, showNotice,
  el, paintShell,
} = SmartOps;

const errorBox = document.getElementById("error");
const rowsBody = document.getElementById("signin-rows");

// Systems whose sign-in window is open right now, so polling stops when nothing
// is in flight rather than hammering the server forever.
const polling = new Set();
const credentialPolling = new Set();

function statusCell(item) {
  if (item.auth_mode === "none") return el("td", {}, [badge("Not needed", "gray")]);
  if (item.auth_mode === "unattended") {
    return el("td", {}, [
      item.credential_stored ? badge("Password saved", "green") : badge("No password yet", "red"),
      item.username ? el("div", { class: "muted hint" }, [item.username]) : null,
    ]);
  }
  if (item.session_exists) {
    const hours = item.session_age_hours != null ? item.session_age_hours.toFixed(1) : "?";
    return el("td", {}, [
      badge("Signed in", "green"),
      el("div", { class: "muted hint" }, [`Saved ${hours} hours ago. Sign in again if a run reports an expired session.`]),
    ]);
  }
  // A saved file that no longer works is not the same as never having signed
  // in, and the difference changes nothing the user does — but it does explain
  // why a system that worked yesterday is asking again today.
  if (item.session?.exists) {
    return el("td", {}, [
      badge("Sign-in expired", "orange"),
      el("div", { class: "muted hint" }, [item.session.reason || "Sign in again."]),
    ]);
  }
  return el("td", {}, [badge("Not signed in", "red")]);
}

function actionCell(item) {
  const cell = el("td", {}, []);
  if (item.auth_mode === "none") {
    cell.appendChild(el("span", { class: "muted", text: "Nothing to do." }, []));
    return cell;
  }
  if (item.auth_mode === "unattended") {
    const open = el("button", { type: "button" }, [
      item.credential_stored ? "Replace securely" : "Open secure window",
    ]);
    open.addEventListener("click", () => startCredentialPrompt(item.system_key, open));
    cell.appendChild(open);
    return cell;
  }

  const progress = item.login_in_progress;
  if (progress?.active) {
    cell.appendChild(el("p", { class: "hint", text: progress.message }, []));
    const done = el("button", { type: "button" }, ["I have signed in"]);
    done.addEventListener("click", () => finishLogin(item.system_key, done));
    const cancel = el("button", { type: "button", class: "secondary small" }, ["Cancel"]);
    cancel.addEventListener("click", async () => {
      await postJSON(`/api/systems/${encodeURIComponent(item.system_key)}/login/cancel`, {}).catch(() => null);
      polling.delete(item.system_key);
      load();
    });
    cell.append(done, cancel);
    polling.add(item.system_key);
    return cell;
  }

  const start = el("button", { type: "button" }, [item.session_exists ? "Sign in again" : "Sign in now"]);
  start.addEventListener("click", async () => {
    start.disabled = true;
    start.textContent = "Opening a browser window…";
    clearError(errorBox);
    try {
      await postJSON(`/api/systems/${encodeURIComponent(item.system_key)}/login`, {});
      polling.add(item.system_key);
      await load();
      pollSoon();
    } catch (err) {
      showError(errorBox, err);
      start.disabled = false;
    }
  });
  cell.appendChild(start);
  if (progress && !progress.active && progress.message) {
    cell.appendChild(el("p", { class: "muted hint", text: progress.message }, []));
  }
  return cell;
}

async function finishLogin(systemKey, button) {
  button.disabled = true;
  button.textContent = "Saving…";
  clearError(errorBox);
  try {
    const result = await postJSON(`/api/systems/${encodeURIComponent(systemKey)}/login/finish`, {});
    polling.delete(systemKey);
    if (result.saved) {
      showNotice(errorBox, "Signed in and saved. Next: record the task you want automated.", {
        label: "Go to Recordings", href: "recordings.html",
      });
    } else {
      showNotice(errorBox, result.message || "The session was not saved. Try signing in again.");
    }
    await load();
    paintShell();
  } catch (err) {
    showError(errorBox, err);
    button.disabled = false;
    button.textContent = "I have signed in";
  }
}

/* While a sign-in window is open the browser is waiting on a human, so the page
   re-checks every couple of seconds — and stops the moment nothing is in flight. */
let pollTimer = null;
function pollSoon() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (!polling.size && !credentialPolling.size) {
      clearInterval(pollTimer);
      pollTimer = null;
      return;
    }
    await Promise.all([load(), loadCredentials()]);
  }, 2500);
}

async function load() {
  try {
    const data = await getJSON("/api/signin");
    rowsBody.innerHTML = "";
    polling.clear();
    if (!data.items.length) {
      rowsBody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, [
        "No systems yet. Add one first, then come back here.",
      ])]));
      return;
    }
    for (const item of data.items) {
      rowsBody.appendChild(el("tr", {}, [
        el("td", {}, [el("strong", {}, [item.name || item.system_key])]),
        el("td", {}, [{
          none: "No sign-in needed",
          session: "You sign in once (SSO and MFA supported)",
          unattended: "Platform types a saved username and password",
        }[item.auth_mode] || item.auth_mode]),
        statusCell(item),
        actionCell(item),
      ]));
    }
    if (polling.size) pollSoon();
  } catch (err) {
    showError(errorBox, err);
  }
}

/* ---------- isolated Windows prompt (unattended systems only) ---------- */

const credentialMessage = document.getElementById("credential-message");

async function startCredentialPrompt(systemKey, button) {
  clearError(errorBox);
  button.disabled = true;
  button.textContent = "Opening secure window…";
  credentialMessage.textContent = "Look for the separate Windows username and password window.";
  try {
    const prompt = await postJSON(`/api/credentials/${encodeURIComponent(systemKey)}/prompt`, {});
    if (prompt.active) credentialPolling.add(systemKey);
    credentialMessage.textContent = prompt.message;
    await Promise.all([loadCredentials(), load()]);
    pollSoon();
  } catch (err) {
    showError(errorBox, err);
    button.disabled = false;
    button.textContent = "Open secure window";
  }
}

async function loadCredentials() {
  const body = document.getElementById("rows");
  try {
    const data = await getJSON("/api/credentials");
    body.innerHTML = "";
    const note = document.getElementById("system-note");

    if (!data.items.length) {
      note.textContent = "No system is set to sign in with a saved username and password, so there is nothing to save here.";
      body.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["Nothing saved"])]));
      return;
    }
    note.textContent = "Credentials stay in Windows Credential Manager and are reused whenever an automatic run needs to sign in.";
    const prompts = await Promise.all(data.items.map(item =>
      getJSON(`/api/credentials/${encodeURIComponent(item.system_key)}/prompt`).catch(() => null)
    ));
    credentialPolling.clear();
    data.items.forEach((item, index) => {
      const prompt = prompts[index];
      if (prompt?.active) credentialPolling.add(item.system_key);

      const open = el("button", { type: "button" }, [
        item.stored ? "Replace securely" : "Open secure window",
      ]);
      open.disabled = Boolean(prompt?.active);
      open.addEventListener("click", () => startCredentialPrompt(item.system_key, open));
      const remove = el("button", { type: "button", class: "danger small" }, ["Delete"]);
      remove.disabled = !item.stored;
      remove.addEventListener("click", async () => {
        try {
          await deleteJSON(`/api/credentials/${encodeURIComponent(item.system_key)}`);
          credentialMessage.textContent = "Saved credential deleted.";
          await Promise.all([loadCredentials(), load()]);
        } catch (err) { showError(errorBox, err); }
      });
      body.appendChild(el("tr", {}, [
        el("td", {}, [item.system_key]),
        el("td", {}, [item.username || "—"]),
        el("td", {}, [
          item.stored ? badge("Saved", "green") : badge("Not saved", "red"),
          prompt?.message ? el("div", { class: "muted hint", text: prompt.message }, []) : null,
        ]),
        el("td", {}, [el("div", { class: "table-actions" }, [open, remove])]),
      ]));
    });
    if (credentialPolling.size) pollSoon();
  } catch (err) {
    showError(errorBox, err);
  }
}

document.getElementById("refresh").addEventListener("click", loadCredentials);

load();
loadCredentials();
