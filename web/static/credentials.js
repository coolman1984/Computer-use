(() => {
  const error = document.getElementById("error");
  const form = document.getElementById("credential-form");
  const system = document.getElementById("system");
  const rows = document.getElementById("rows");
  const message = document.getElementById("message");
  const systemNote = document.getElementById("system-note");
  const submitButton = form.querySelector("button[type=submit]");
  const esc = value => String(value ?? "").replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const request = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: { "X-SmartOps-Request": "web", ...(options.headers || {}) } });
    if (!response.ok) throw new Error(await SmartOps.errorMessage(response));
    return response.json().catch(() => ({}));
  };
  // Both sign-in modes belong on this page: a user landing here needs to know
  // the state of every system, not only the ones a stored credential applies
  // to. Session-mode systems are signed in from the terminal, so show that
  // command rather than leaving them invisible here.
  const renderSignInStatus = items => {
    const body = document.getElementById("signin-rows");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty">No systems defined yet.</td></tr>';
      return;
    }
    body.innerHTML = items.map(s => {
      const connected = Boolean(s.session_exists);
      let status, todo;
      if (s.auth_mode === "none") {
        status = '<span class="badge badge-gray">No sign-in needed</span>';
        todo = "Nothing — this system is public.";
      } else if (s.auth_mode === "unattended") {
        status = connected ? '<span class="badge badge-green">Connected</span>' : '<span class="badge badge-blue">Credential login</span>';
        todo = "Store the username and password in the form below.";
      } else if (connected) {
        const hours = s.session_age_hours != null ? s.session_age_hours.toFixed(1) : "?";
        status = '<span class="badge badge-green">Connected</span>';
        todo = `Saved session is ${esc(hours)} hours old. Sign in again when it expires.`;
      } else {
        status = '<span class="badge badge-red">Not signed in</span>';
        todo = `Run in a terminal: python -m smartops login ${esc(s.key)}`;
      }
      return `<tr><td><strong>${esc(s.name || s.key)}</strong><div class="muted hint">${esc(s.key)}</div></td><td>${esc(s.auth_mode)}</td><td>${status}</td><td class="muted">${todo}</td></tr>`;
    }).join("");
  };

  const load = async () => {
    try {
      const systems = await SmartOps.getJSON("/api/systems");
      const creds = await SmartOps.getJSON("/api/credentials");
      // Only a system defined with auth.mode: unattended can use a stored
      // credential at all (session-mode systems rely on the one-time manual
      // browser login instead) — but leaving the dropdown just empty with no
      // explanation forces the user into a bare "Please select an item"
      // browser popup with zero context on why nothing is selectable.
      renderSignInStatus(systems.items);
      const eligible = systems.items.filter(s => s.auth_mode === "unattended");
      system.innerHTML = eligible.map(s => `<option value="${esc(s.key)}">${esc(s.name)} (${esc(s.key)})</option>`).join("");
      system.disabled = eligible.length === 0;
      submitButton.disabled = eligible.length === 0;
      systemNote.textContent = eligible.length === 0
        ? "No system is currently defined with auth.mode: unattended, so there is nothing to attach a stored credential to. A system whose sign-in is an interactive SSO popup (like an AD login) generally can't be driven this way — see auth.mode: session instead."
        : "";
      rows.innerHTML = creds.items.length ? creds.items.map(c => `<tr><td>${esc(c.system_key)}</td><td>${c.stored ? esc(c.username) : "—"}</td><td>${c.stored ? '<span class="badge badge-green">Stored</span>' : '<span class="badge badge-gray">Not configured</span>'}</td><td>${c.stored ? `<button class="secondary delete-credential" data-key="${esc(c.system_key)}" type="button">Remove</button>` : ""}</td></tr>`).join("") : '<tr><td colspan="4" class="empty">No unattended systems configured.</td></tr>';
      rows.querySelectorAll(".delete-credential").forEach(button => button.addEventListener("click", async () => {
        if (!confirm("Remove this stored credential?")) return;
        try { await request(`/api/credentials/${encodeURIComponent(button.dataset.key)}`, { method: "DELETE" }); await load(); }
        catch (e) { SmartOps.showError(error, e); }
      }));
    } catch (e) { SmartOps.showError(error, e); }
  };
  form.addEventListener("submit", async event => {
    event.preventDefault(); message.textContent = "";
    try {
      await request(`/api/credentials/${encodeURIComponent(system.value)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: document.getElementById("username").value, password: document.getElementById("password").value }) });
      form.reset(); message.textContent = "Credential saved securely in Windows Credential Manager."; await load();
    } catch (e) { SmartOps.showError(error, e); }
  });
  document.getElementById("refresh").addEventListener("click", load);
  load();
})();
