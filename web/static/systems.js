/* Systems: the first real step of the workflow. Shows every defined system,
   whether it is signed in, and what to do next when it is not. */
const { getJSON, postJSON, badge, formatDate, showError, el, link } = SmartOps;
const errorBox = document.getElementById("error");

// Each sign-in mode needs a different action from the user, so say which one
// plainly rather than leaving a bare status badge with no next step.
function signInCell(system) {
  if (system.auth_mode === "none") {
    return el("td", {}, [badge("No sign-in needed", "gray")]);
  }
  if (system.auth_mode === "unattended") {
    return el("td", {}, [
      badge(system.session_exists ? "Connected" : "Credential login", system.session_exists ? "green" : "blue"),
      el("div", { class: "muted hint" }, ["Store the username and password under Sign-in, then SmartOps logs in by itself."]),
    ]);
  }
  if (!system.session_exists) {
    return el("td", {}, [
      badge("Not signed in", "red"),
      el("div", { class: "muted hint" }, [`Run once in a terminal: python -m smartops login ${system.key}`]),
    ]);
  }
  const hours = system.session_age_hours != null ? system.session_age_hours.toFixed(1) : "?";
  return el("td", {}, [
    badge("Connected", "green"),
    el("div", { class: "muted hint" }, [`Saved session is ${hours} hours old. Sign in again when it expires.`]),
  ]);
}

function scheduleText(schedule) {
  if (!schedule || !schedule.enabled) return "Manual only";
  if (schedule.daily_at) return `Daily at ${schedule.daily_at}`;
  if (schedule.every_seconds) return `Every ${Math.round(schedule.every_seconds / 60)} min`;
  return "Manual only";
}

async function load() {
  const systemsBody = document.getElementById("systems-body");
  const reportsBody = document.getElementById("reports-body");
  try {
    const data = await getJSON("/api/systems");
    systemsBody.innerHTML = "";
    reportsBody.innerHTML = "";
    // Showing the directory the server actually read makes the usual setup
    // mistake self-diagnosing: SMARTOPS_SYSTEMS_DIR set in a different
    // terminal than the one running the server, so it silently falls back to
    // the in-repo examples.
    if (data.directory) document.getElementById("systems-dir").textContent = data.directory;

    if (!data.items.length) {
      systemsBody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, [
        "No systems defined yet — add a .yaml file to your systems directory and restart the server.",
      ])]));
      reportsBody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["No reports yet"])]));
      return;
    }

    for (const system of data.items) {
      systemsBody.appendChild(el("tr", {}, [
        el("td", {}, [
          el("strong", {}, [system.name || system.key]),
          el("div", { class: "muted hint" }, [system.key]),
        ]),
        el("td", {}, [system.auth_mode]),
        signInCell(system),
        el("td", {}, [String(system.reports.length)]),
      ]));

      for (const report of system.reports) {
        const button = el("button", {}, ["Collect now"]);
        button.addEventListener("click", async () => {
          button.disabled = true;
          const original = button.textContent;
          button.textContent = "Collecting…";
          try {
            const run = await postJSON(`/api/systems/${encodeURIComponent(system.key)}/${encodeURIComponent(report.key)}/collect`, {});
            location.href = `run.html?id=${encodeURIComponent(run.id)}`;
          } catch (err) {
            showError(errorBox, err);
            button.textContent = original;
            button.disabled = false;
          }
        });
        reportsBody.appendChild(el("tr", {}, [
          el("td", {}, [system.name || system.key]),
          el("td", {}, [
            el("strong", {}, [report.title || report.key]),
            el("div", { class: "muted hint" }, [report.key]),
          ]),
          el("td", {}, [scheduleText(report.schedule)]),
          el("td", {}, [button]),
        ]));
      }
    }

    if (!reportsBody.children.length) {
      reportsBody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "empty" }, ["No reports defined on any system"])]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

load();
