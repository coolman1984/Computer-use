# Implementation Roadmap

## Phase 0 — Repository foundation
- Document the vision.
- Agent rules.
- Code structure.
- Settings.
- Smoke tests.

## Phase 1 — Core (done ✅)
- Local FastAPI. ✅
- SQLite database with migrations. ✅
- A resumable Workflow/Run/Step model. ✅
- An ordered event log. ✅
- Contracts for the browser, validation, agents, and alerting. ✅
- Remaining for this phase: the Playwright adapter and a real file download (package S-02).

## Phase 2 — Reliable data collection
- Multiple systems.
- Multiple tabs.
- A queue.
- Retry.
- File validation.
- Raw data archiving.
- Trace for incidents.

## Phase 3 — The web app
- Dashboard.
- Workflows.
- Runs.
- Incidents.
- Logs.
- Agents.
- Chat side panel.

## Phase 4 — Agent Manager
- Run Codex CLI.
- Run Claude Code CLI.
- Stream output to the UI.
- Read-only / experiment / execute.
- A complete log.

## Phase 5 — Alerting and early warning
- Baselines.
- Degradation detection.
- Alert rules.
- Incident pack creation.

## Phase 6 — Self-healing
- Known fixes.
- Escalation.
- Sandbox.
- Tests.
- Rollback.
- Approval gates.

## Phase 7 — Vision
- Screenshots.
- Vision adapter.
- Relative coordinates.
- A full fallback for difficult interfaces.

## Phase 8 — Department automation
- Run projects once their inputs are complete.
- Dependency graph.
- Shared history.
- Cross-department insights.

## First pilot scope

- 3 systems.
- 5 reports.
- One automation project.
- One lateness alert.
- One incident pack.
- Codex analysis-only at first.
