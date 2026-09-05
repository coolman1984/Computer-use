/* Sign-in: step 3, entirely inside the app.

   This page used to tell the user to open a terminal and run
   `python -m smartops login <system>`. That single instruction broke the whole
   promise of the product for a non-technical user. Now the server opens the
   browser window, this page polls the attempt, and the user confirms with a
   button when they are done. */

const {
  getJSON, postJSON, putJSON, deleteJSON, badge, showError, clearError, showNotice,
  el, paintShell,
} = SmartOps;

const errorBox = document.getElementById("error");
const rowsBody = document.getElementById("signin-rows");

// Systems whose sign-in window is open right now, so polling stops when nothing
// is in flight rather than hammering the server forever.
const polling = new Set();

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
    cell.appendChild(el("span", { class: "muted", text: "Save the username and password below." }, []));
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
    if (!polling.size) {
      clearInterval(pollTimer);
      pollTimer = null;
      return;
    }
    await load();
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

/* ---------- stored passwords (unattended systems only) ---------- */

const credentialForm = document.getElementById("credential-form");
const message = document.getElementById("message");

async function loadCredentials() {
  const body = document.getElementById("rows");
  const select = document.getElementById("system");
  try {
    const data = await getJSON("/api/credentials");
    body.innerHTML = "";
    select.innerHTML = "";
    const note = document.getElementById("system-note");
    const submit = credentialForm.querySelector("button[type=submit]");

    // Only unattended systems appear here at all; saying so beats an empty
    // dropdown the user cannot explain.
    if (!data.items.length) {
      note.textContent = "No system is set to sign in with a saved username and password, so there is nothing to save here.";
      select.disabled = true;
      submit.disabled = true;
      body.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["Nothing saved"])]));
      return;
    }
    note.textContent = "";
    select.disabled = false;
    submit.disabled = false;
    for (const item of data.items) {
      select.appendChild(el("option", { value: item.system_key }, [item.system_key]));
      const remove = el("button", { type: "button", class: "danger small" }, ["Delete"]);
      remove.disabled = !item.stored;
      remove.addEventListener("click", async () => {
        try {
          await deleteJSON(`/api/credentials/${encodeURIComponent(item.system_key)}`);
          message.textContent = "Password deleted.";
          await Promise.all([loadCredentials(), load()]);
        } catch (err) { showError(errorBox, err); }
      });
      body.appendChild(el("tr", {}, [
        el("td", {}, [item.system_key]),
        el("td", {}, [item.username || "—"]),
        el("td", {}, [item.stored ? badge("Saved", "green") : badge("Not saved", "red")]),
        el("td", {}, [remove]),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

credentialForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(errorBox);
  const systemKey = document.getElementById("system").value;
  const button = credentialForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await putJSON(`/api/credentials/${encodeURIComponent(systemKey)}`, {
      username: document.getElementById("username").value,
      password: document.getElementById("password").value,
    });
    message.textContent = "Saved securely. Next: record the task you want automated.";
    // Clear the password from the page as soon as it has been handed over.
    document.getElementById("password").value = "";
    await Promise.all([loadCredentials(), load()]);
    paintShell();
  } catch (err) {
    showError(errorBox, err);
  } finally {
    button.disabled = false;
  }
});

document.getElementById("refresh").addEventListener("click", loadCredentials);

load();
loadCredentials();
