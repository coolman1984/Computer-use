# Briefing for Fable 5.1 — SmartOps Project Review

This document exists to give Fable 5.1 (or any other outside reviewer) everything
needed to form an informed opinion on this project **without reading the whole
repo**. All other docs in this repo are written in Arabic for the primary team;
this one is English on purpose, as a self-contained package for external review.

If you are Fable 5.1 reading this: the goal is your independent opinion on the
plan, the architecture, the current build, and the open decisions listed in
section 8. Push back wherever you disagree — this brief is not asking for
validation, it's asking for a second opinion.

---

## 1. One-paragraph summary

**SmartOps** is a local-first "operations control center" for a non-technical
end user: it logs into multiple internal web systems, extracts/downloads
reports through the cheapest reliable method available (network request → DOM
→ self-healing selectors → vision → full desktop control, in that order),
validates the files it gets, runs department automation on top of that data,
watches for performance degradation, opens incidents with evidence bundles,
and can call Codex CLI / Claude Code CLI as background agents to diagnose and
(eventually, behind approval gates) fix problems. It is explicitly **not**
meant to be "a pile of scrapers" — it's meant to be a system that knows what
should happen, when, what's normal, what isn't, and how to recover.

## 2. Why it exists (the problem)

- Many internal systems, many tabs, manual clicking to pull reports.
- Some pages are readable from DOM, some aren't (canvas/custom-rendered).
- Screen resolutions differ across machines, so absolute coordinates are banned.
- Some reports can be fetched via an authorized network request instead of
  simulating dozens of clicks — that's always preferred when possible.
- Sessions expire, filenames drift, downloads can be partial/duplicate/stale.
- Every step of the process must stay reviewable and reversible.
- The end user is non-technical — the web app is the primary interface, not a
  terminal or code files.

## 3. Target architecture

```text
Local Web App (dashboard, workflows, incidents, history, agents, chat)
        │
        ▼
   Local Control Plane  (source of truth for run state, locking, retry, resume)
        │
 ┌──────┼────────┬───────────┬─────────────┐
 ▼      ▼        ▼           ▼             ▼
Workflow Adaptive  Data &    Observability  AI Agents
Engine  Browser    History   & Alerting     (Codex/Claude)
        (Network/  (SQLite +
         DOM/      DuckDB/
         Vision/   Parquet)
         Desktop)
```

**Component boundaries:**
- *Control Plane* — authoritative run state, identity, dependencies, locking, retry, pause/resume.
- *Workflow Engine* — steps are explicit resumable state, not a long unrecoverable script.
- *Browser Engine* — adaptive multi-layer site access/extraction.
- *Data Manager* — raw files, hashes, validation results, historical/derived data.
- *Observability* — metrics, tracing, error/degradation detection.
- *Incident Manager* — builds a full evidence bundle and routes it to diagnosis.
- *Agent Manager* — decides which agent + reasoning level, logs every input/output/change.
- *Web App* — the primary UI; core functionality must never require a CLI.

**Run state machine:** `queued → running → waiting → retrying → succeeded | failed | cancelled`.
Every step records started_at, finished_at, retries, error classification, evidence references.

## 4. The 5-layer browser extraction ladder

Cheapest/most-reliable first, escalate only on failure:

1. **Network / direct download** — replay an authorized export request from the
   same user session instead of clicking through the UI.
2. **DOM / Playwright** — elements, frames, popups, tabs, filters, downloads, smart waits.
3. **Self-healing web actions** — Stagehand-style: try the known selector,
   fall back to smart discovery, persist the new path only after verification.
4. **Vision** — for pages that don't expose readable structure (Midscene / Browser Use style).
5. **Desktop-level vision control** — last resort, full UI control (UI-TARS / Agent TARS style).

Camoufox is explicitly a secondary/experimental option, never the core engine.
No absolute screen coordinates anywhere — semantic elements first, relative
coordinates inside a screenshot as fallback.

## 5. Tech stack and why

| Tech | Role | Why this one |
|---|---|---|
| Python 3.11 | platform language | best integration across files, browser, analysis, agents |
| FastAPI | local service + web API | fast, simple, automatic API docs |
| WebSocket | live updates + side chat | user watches runs in real time |
| SQLite | operational state/log | single file, no server, works locally on Windows |
| DuckDB + Parquet | analytics/long history | fast local analysis of large files without a heavy DB |
| Playwright | core browser engine | reliable control of tabs/sessions/downloads/tracing |
| Codex CLI / Claude Code CLI | background agents | diagnosis/fixes inside a permission policy |
| OpenTelemetry | metrics/tracing (later) | needed once the system grows |

Rule: any "smart" or vision-based component is an **optional adapter behind a
port/contract**, never a hard dependency. If you rip it out, the core still runs.

## 6. Governance, security, and agent permissions

- Repo is currently **public** — no secrets, cookies/sessions, API keys,
  internal URLs, employee/customer data, real raw data files, production DB
  copies, or sensitive screenshots are allowed in it.
- Least privilege: every worker/agent gets only the tools its task needs.
- Action tiers:
  - **Green** — retry, reopen page, re-download, validate file (fully automatic).
  - **Yellow** — modify code/workflow inside a sandbox + run tests.
  - **Red** — production changes, data deletion, permission/DB changes — require human approval.
- Agent rules: read is separate from write; production execution is separate
  from experimentation; every change has a diff, a log, and tests; every
  change is reversible; no two agents touch the same part concurrently without a lock.
- **As of today, the AI agent is off by default**, and only `read_only`
  (analyze) mode is actually implemented and wired in. Any other `mode` is
  rejected with an explicit configuration error at service build time — it is
  not silently allowed broader access. Experiment and Execute modes are
  intentionally unbuilt pending sandbox + tests + human-approval design.

## 7. Current build status (what's actually implemented, not just planned)

The core (P1) and the first adapter round (P2–P4) are done and tested:
config, SQLite + migrations, sequential event log, resumable workflow engine
with locking + classified retry, automatic incident opening with a full
evidence bundle, notifications, live WebSocket event streaming, a real
Playwright browser adapter, a file validator, YAML-driven system/report
profiles, a background worker with bounded concurrency, Parquet/DuckDB
analytical archiving, an AI agent runner restricted to analyze-only mode, and
a static web app served at `/app`.

Repo layout (for reference, not required reading):

```
src/smartops/
  core/        clock, errors, ids
  domain/      enums, models
  engine/      contracts, registry, retry, runner (workflow engine)
  events/      bus, log
  ports/       agents.py, browser.py, notify.py, validation.py   ← contracts
  adapters/
    agents/    cli_runner.py, commands.py     (Codex/Claude runner, analyze-only)
    browser/   playwright_engine.py
    history/   archiver.py                    (Parquet + DuckDB)
    incidents/ pack.py
    notify/    local.py
    validation/local.py
  storage/     db.py, paths.py, repositories.py
  workflows/   builtin.py, profiles.py
  api/         app.py (FastAPI), ws.py (WebSocket)
  worker.py, services.py (composition root), main.py
web/           static web app served at /app
tests/         16 test files, all green (~107 tests total)
```

Ten self-contained "Sonnet packets" (S-01..S-10) filled in every adapter body
behind a pre-written port/contract; all ten are marked done. All safe, local
adapters (file validator, browser engine, local alert log, analytical
archive, system profiles) are wired into `services.py` by default, so
`collect.report` runs for real with no fake/mock adapter — nothing touches a
real network or external process unless a run step actually calls it. This
default-wiring decision plus the agent's analyze-only-by-default decision were
both made explicitly (see `docs/DECISION_LOG.md`, entries D018–D019) as the
"Opus-level" call after all ten Sonnet packets landed.

**Pilot scope this build targets:** 3 systems, 5 reports, one automation
project, one delay alert, one full incident, one analyze-only agent run. That
scope is now functionally complete; the plan is to prove stability here before
widening.

## 8. What's next — the actual open decisions

This is the part most worth Fable's opinion on. Nothing below is a coding
task in the "fill the function body" sense — each is a design/policy call:

1. **Experiment and Execute agent modes.** Only `read_only` exists today.
   Turning on Experiment (sandbox edit + test) and Execute (deploy a fix that
   passed tests and policy allows) needs: sandbox isolation design, a rollback
   mechanism, and a concrete human-approval gate — before any code is written,
   not after.
2. **P5 — Early warning / anomaly detection.** Baselines and degradation
   detection (e.g. a report's load time creeping 8s → 10s → 15s → 23s → 31s)
   aren't built. Needs a decision on what "abnormal" means statistically
   (fixed thresholds vs. rolling baselines vs. something else) before Sonnet
   can implement the rule layer.
3. **P6 — Self-healing.** Known-fix lookup, sandbox trial, test-gated deploy,
   automatic rollback on regression. Directly depends on decision #1.
4. **P7 — Vision fallback adapter.** Layer 4 of the extraction ladder
   (Midscene/Browser Use-style) isn't built; only network+DOM (layers 1–2) are
   real today. Self-healing selectors (layer 3) and desktop-level vision
   (layer 5) are also unbuilt.
5. **P8 — Cross-department dependency graph.** Connecting department
   automations so an upstream failure visibly affects downstream processes.
   Nothing built yet; this is architecture-level (Opus-tier per the project's
   own model-assignment rule), not an adapter fill-in.
6. **Escalation ladder tuning.** The design calls for: known fix → cheap agent
   → Codex medium → Codex high → strong Claude model → human. The *policy*
   for when to step up (risk, retry count, historical success rate, confidence,
   cost) is specified narratively but not yet encoded as a concrete rule set.
7. **OpenTelemetry integration** — explicitly deferred, no timeline set.
8. **Repo visibility.** The repo is public today and the docs explicitly
   forbid putting any real internal detail in it. At some point real
   deployment needs a private repo or secure knowledge store (`docs/DECISION_LOG.md`, D012) — no plan yet for when/how that split happens.

## 9. Questions worth asking Fable directly

- Is the 5-layer extraction ladder (network → DOM → self-healing → vision →
  desktop) the right ordering/set of layers, or is there a simpler design
  that gets the same reliability with less surface area?
- Is SQLite (operational) + DuckDB/Parquet (analytical) the right split at
  this scale, or premature complexity for a 3-system pilot?
- Is gating Experiment/Execute agent modes behind a from-scratch sandbox +
  rollback design the right call, or is there a lighter-weight way to get
  partial self-healing sooner without the full mechanism?
- Any red flags in the security/governance model (section 6) for a system
  that will eventually touch real internal credentials and data?
- Given the project's own model-assignment rule (contracts/architecture/
  security → strong model; fill-in-the-body work → cheaper model), does the
  list of "what's next" in section 8 look correctly triaged, or is something
  miscategorized?

## 10. Where to look for more (only if needed)

Everything above is already distilled from these; you shouldn't need to open
them, but they're the source of truth if something here seems incomplete:

- `docs/PROJECT_CONTEXT.md` — motivation and constraints (Arabic)
- `docs/MASTER_PLAN.md` — full plan, all subsystems (Arabic)
- `docs/EXECUTION_PLAN.md` — phase table, model-assignment rule, token policy (Arabic)
- `docs/ARCHITECTURE.md`, `docs/BROWSER_EXTRACTION_ENGINE.md`,
  `docs/AI_AGENT_ORCHESTRATION.md`, `docs/OBSERVABILITY_SELF_HEALING.md`,
  `docs/SECURITY_GOVERNANCE.md`, `docs/IMPLEMENTATION_ROADMAP.md` — per-topic detail (Arabic)
- `docs/DECISION_LOG.md` — D001–D019, every architectural decision with its reason (Arabic)
- `docs/AGENT_TASK_PACKETS.md` — the ten self-contained implementation packets, S-01..S-10 (Arabic)
- `AGENTS.md` — mandatory rules for any coding agent working in this repo (Arabic)
