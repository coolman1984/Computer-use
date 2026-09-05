(() => {
  const error = document.getElementById("error");
  const form = document.getElementById("credential-form");
  const system = document.getElementById("system");
  const rows = document.getElementById("rows");
  const message = document.getElementById("message");
  const esc = value => String(value ?? "").replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const request = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: { "X-SmartOps-Request": "web", ...(options.headers || {}) } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail?.message || body.detail || "Request failed.");
    return body;
  };
  const load = async () => {
    try {
      const systems = await SmartOps.getJSON("/api/systems");
      const creds = await SmartOps.getJSON("/api/credentials");
      system.innerHTML = systems.items.filter(s => s.auth_mode === "unattended").map(s => `<option value="${esc(s.key)}">${esc(s.name)} (${esc(s.key)})</option>`).join("");
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
