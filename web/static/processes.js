/* The automations list: every recorded task and exactly which gate it is at. */

const {
  getJSON, statusBadge, badge, showError, el, link, PROCESS_STATUS_LABELS,
} = SmartOps;

const errorBox = document.getElementById("error");

/* The single next action for each stage. Kept in one place so the list, the
   detail page and the server all describe the gates the same way. */
const NEXT_ACTION = {
  draft: "Test it",
  testing: "Testing now…",
  tested: "Approve it",
  test_failed: "Fix and test again",
  approved: "Run it or schedule it",
  retired: "Retired",
};

function scheduleText(process) {
  if (!process.is_scheduled) return badge("No", "gray");
  const schedule = process.schedule;
  if (schedule.daily_at) return badge(`Daily at ${schedule.daily_at}`, "green");
  if (schedule.every_seconds) return badge(`Every ${Math.round(schedule.every_seconds / 60)} min`, "green");
  return badge("Yes", "green");
}

async function load() {
  const body = document.getElementById("rows");
  try {
    const data = await getJSON("/api/processes?limit=200");
    body.innerHTML = "";
    if (!data.items.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "5", class: "empty" }, [
        "No automations yet. Finish a recording, then turn it into one.",
      ])]));
      return;
    }
    for (const process of data.items) {
      body.appendChild(el("tr", {}, [
        el("td", {}, [
          el("strong", {}, [process.name]),
          el("div", { class: "muted hint" }, [`${process.action_count} step(s) · version ${process.version}`]),
        ]),
        el("td", {}, [process.system_key]),
        el("td", {}, [
          statusBadge(PROCESS_STATUS_LABELS, process.status),
          el("div", { class: "muted hint" }, [NEXT_ACTION[process.status] || ""]),
        ]),
        el("td", {}, [scheduleText(process)]),
        el("td", {}, [link("Open", `process.html?id=${encodeURIComponent(process.id)}`)]),
      ]));
    }
  } catch (err) {
    showError(errorBox, err);
  }
}

load();
