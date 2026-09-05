# SmartOps Master Plan

## 1. The core idea

SmartOps is a local, smart operations center that collects raw data from
multiple web systems, validates it, runs business automations, links
departments and processes, monitors performance and sites, detects failures
and degradation early, then uses AI agents for diagnosis, repair, and
organized escalation.

The goal is not to build separate scripts, but an **operating system for
automation** that knows what should happen, when, what is normal, what is
not, how to recover from problems, and when it needs human intervention.

---

## 2. The full process journey

```text
Multiple sources and systems
↓
Extract data by the best available method
↓
Download and collect raw data
↓
Check file health and data quality
↓
Run the department's workflow
↓
Link the workflow to other departments and data
↓
Update history and monitoring dashboards
↓
Monitor performance, lateness, and anomalies
↓
Early warning or an incident when needed
↓
Retrieve evidence and context
↓
A known fix or an AI agent
↓
Experiment and test
↓
Safe deployment or rollback
↓
Record the lesson in the knowledge base
```

---

## 3. The web app is the operations center

The non-technical user works primarily with a local web interface containing:

- A dashboard for overall status.
- A workflow list.
- Start, stop, and retry.
- Creating a new process.
- Scheduling and dependencies.
- Live monitoring.
- Searchable logs.
- Incidents.
- History.
- Agents.
- A knowledge base.
- A side chat.

The chat supports commands such as:

- Why is a particular report slow today?
- Run the morning meeting processes.
- Collect two systems' files and compare them by date.
- Create a new workflow.
- Fix the problem on a test branch and test it.
- Show what changed since yesterday.

Chat modes:

1. **Analyze**: read and analyze only.
2. **Experiment**: modify a sandbox branch and test.
3. **Execute**: run what policy permits.

---

## 4. The adaptive browser engine

No single tool is ideal for every site, so we use a five-layer ladder:

### Layer 1 — Network / Direct Download
If the export button generates a request that can be understood and
replayed within the same authorized session, we use that request directly.

### Layer 2 — DOM / Playwright
For elements, frames, windows, tabs, filters, and downloads.

### Layer 3 — Self-Healing Web Actions
When the page changes, we use a Stagehand-like approach: the fixed method
first, then smart discovery, then caching the new path once verified.

### Layer 4 — Vision
For pages that expose no readable elements. We draw on Midscene and Browser Use ideas.

### Layer 5 — Desktop-Level Vision
For the rare cases needing full UI control, using UI-TARS and Agent TARS ideas.

Camoufox remains a secondary, experimental option, not the primary engine.

---

## 5. Different screen resolutions

Relying on fixed screen coordinates is forbidden.

Priority order:

1. A semantic element.
2. A region within the element.
3. Relative coordinates within a screenshot.

The background browser can also be run at a uniform viewport size.

---

## 6. Many tabs and sessions

We use:

- A central queue.
- Bounded, dynamic concurrency.
- Isolated browser contexts.
- Memory and CPU monitoring.
- Backpressure if the system or sites are slow.
- Different retry policies depending on the error type.

There is no goal of an unlimited number of tabs; the goal is the highest stable throughput.

---

## 7. Defining each system

Every system or site has a profile that stores:

- The name.
- The entry point.
- The authorized authentication method.
- The pages and reports.
- The best extraction method.
- Is DOM available?
- Is vision required?
- Is direct download possible?
- The expected files.
- The normal duration.
- Retry rules.
- Alert rules.
- Known problems and their fixes.

---

## 8. The Raw Data Center

Every download is recorded with metadata:

- The system.
- The report.
- The request and download time.
- The requested period.
- The original name and the standard name.
- The size.
- The hash.
- The row count, if available.
- The validation result.

Proposed layout:

```text
data/raw/YYYY/MM/DD/<system>/<report>/
```

Raw data is never uploaded to GitHub.

---

## 9. File validation

A successful download does not mean a successful process.

Must check:

- The file exists.
- It is not zero-sized.
- It opens without corruption.
- The date is correct.
- The expected sheets or columns exist.
- The row count is sane.
- It is not a duplicate.
- It is not an old copy by mistake.

Failing any condition generates a clear event and may open an incident.

---

## 10. The Workflow Engine

Every process is defined as resumable state instead of a fragile sequential script.

The core statuses:

```text
queued
running
waiting
retrying
succeeded
failed
cancelled
```

Every step has:

- input.
- output.
- started_at.
- finished_at.
- retries.
- error classification.
- evidence references.

---

## 11. Running department automation

After the inputs arrive and are validated:

```text
Raw data ready
↓
Transform / clean
↓
Business rules
↓
KPIs / insights
↓
History update
↓
Dashboard / reports
```

Department projects are plugins or workflows connected to the same control
plane rather than separate, isolated programs.

---

## 12. Linking departments

Dependencies are represented as a dependency graph.

General examples:

```text
Demand / plan
↓
Production
↓
Material need
↓
Inventory / purchasing
↓
Workforce / maintenance / quality
↓
Cost / finance
```

The goal is detecting the impact of a problem in one process on its dependent processes.

---

## 13. Database and history

### SQLite
For operational state:
- workflows
- runs
- steps
- events
- incidents
- alerts
- files
- agents
- approvals
- versions

### DuckDB
For fast local analysis and joining across large historical files.

### Parquet
For long-term analytical history.

---

## 14. Event Log

Everything that happens is logged as an event, such as:

```text
08:00:02 run_started
08:00:05 browser_opened
08:00:12 report_opened
08:00:18 filter_applied
08:00:32 export_requested
08:01:03 latency_warning
08:01:20 download_failed
08:01:21 retry_started
08:01:39 file_validated
08:01:40 run_succeeded
```

The log is searchable inside the web app, not just hidden text files.

---

## 15. The Incident Pack

On a significant problem, evidence is frozen into a pack that includes:

- A summary.
- The error.
- The workflow version.
- A screenshot.
- The browser trace.
- Network evidence.
- Expected vs. actual files.
- An environment snapshot.
- Previous similar runs.
- Previous agent attempts.

This is the AI agent's primary input.

---

## 16. Early warning

We do not wait for the failure.

We watch trends such as:

- Increasing page load time.
- Increasing download time.
- Increasing retries.
- An abnormal drop or spike in file size.
- Delayed data arrival.
- Repeated session expiry.
- A rising error rate.

Alert levels:

- Green.
- Yellow.
- Orange.
- Red.
- Critical.

---

## 17. The AI Agent Manager

The platform can run Codex CLI or Claude Code CLI in the background and show progress in the chat and logs.

For every agent run we record:

- The reason.
- The agent and model.
- The reasoning level.
- The context sent.
- The files read.
- The tools used.
- The changes.
- The tests.
- The result.
- The time taken.
- Whether it was escalated.

---

## 18. The escalation ladder

```text
Known fix
↓
Cheap agent
↓
Codex, medium reasoning
↓
Codex, high reasoning
↓
A stronger Claude model
↓
Human
```

Escalation depends not only on failure, but on risk, confidence, and history.

---

## 19. Learning from past incidents

Before calling a large model:

1. Search for a similar incident.
2. Learn its earlier root cause.
3. Learn the fix that was used.
4. Learn its success rate.
5. Try it if it is safe and appropriate.

Every resolved incident adds a new knowledge entry.

---

## 20. Self-Healing

Fixes have three levels:

### Auto-Safe
- retry.
- reopen page.
- restart browser session.
- re-download.
- wait and retry.

### Test-First
- selector fix.
- workflow change.
- timeout adjustment.
- code patch.

Tried in a sandbox first.

### Human Approval
- destructive data changes.
- permissions.
- production service changes.
- sensitive database operations.

---

## 21. Versioning and Rollback

Any agent that modifies a workflow or code:

```text
v117
↓
patch
↓
v118 candidate
↓
tests
↓
release or rollback
```

If results degrade after deployment, an automatic rollback can occur per policy.

---

## 22. Maintaining sites and services

With official permission and access, the platform can monitor internal sites and services via:

- availability.
- latency.
- error rate.
- login health.
- report generation.
- download success.
- service/database health, when authorized endpoints are available.

The platform never attempts to bypass permissions to reach servers.

---

## 23. Watchdog

The platform itself needs monitoring:

- API service.
- database.
- worker queue.
- browser workers.
- agent processes.
- disk space.
- event writer.

Any failure in the monitoring platform itself must be detected by as independent a watchdog as possible.

---

## 24. Security rules

- No secrets in Git.
- No raw company data in the public repository.
- No sending external data without an approved policy.
- The least privilege possible.
- Every sensitive action is reviewable.
- Production is separated from the sandbox.
- Destructive operations are disabled by default.

---

## 25. The core technology

- Python: the core.
- FastAPI: the API and local service.
- WebSocket: live updates and chat.
- Playwright: the primary browser engine.
- SQLite: operations and logs.
- DuckDB: local analysis.
- Parquet: large-scale history.
- OpenTelemetry: a metrics and tracing layer, later.

Other visual or smart components remain optional adapters, not core dependencies at the start.

---

## 26. The first working version

We start with a small, solid scope instead of trying to build the whole city at once:

- 3 systems.
- 5 reports.
- A browser queue.
- Download and validation.
- A raw data store.
- SQLite events.
- A simple dashboard.
- A lateness alert.
- An incident pack.
- Codex in analysis-only mode.

Once this core is stable, we gradually open up self-modification, vision, and linking departments.

---

## 27. Success metrics

- Run success rate.
- File health rate.
- Data collection time.
- Number of manual steps eliminated.
- Time to detect a problem.
- Time to recover.
- Rate of incidents resolved automatically.
- Number of recurring incidents.
- Number of times human intervention was needed.
- Performance stability over time.

---

## 28. The golden rule

**The fastest reliable method first, then intelligence when needed, and every step is reviewable, testable, and reversible.**
