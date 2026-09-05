# The Big Execution Plan (Two Phases: Plan Then Execute)

**Recording Center status:** the recording core, UI, lifecycle, review,
recovery, and automation draft are implemented locally; what remains is an
interactive production G-MES test before daily adoption.

This document is the primary reference before any new work. Its purpose is
three things: how we build, who builds each part, and how to spend the
fewest tokens possible without sacrificing quality.

---

## 1. Summary in five points

1. **SmartOps is an operating system for automation**, not a collection of
   scripts: it knows what should happen, when, what is normal, what is not,
   and how to recover.
2. **The core is already built**: settings, a database, an event log, a
   resumable workflow engine, clear contracts, and an HTTP interface.
3. **What remains is "filling in the blanks"** inside ready-made contracts
   (browser, file validation, agents, alerting, UI).
4. **Division of labor**: architectural decisions, contracts, and complex
   cases → Opus 5 High. Repetitive implementation inside a ready contract →
   Sonnet 5 Medium.
5. **The stopping criterion is always a green test**, not an opinion or a report.

---

## 2. Adopted technologies (and why)

| Technology | Role | Why this one specifically |
|---|---|---|
| Python 3.11 | The platform language | Strongest integration with files, the browser, analysis, and agents |
| FastAPI | The local service and web interface | Fast, simple, and automatic interface documentation |
| WebSocket | Live updates + the side chat | The user watches the run happen moment by moment |
| SQLite | Operational state and logs | One file, no server, runs locally on Windows |
| DuckDB + Parquet | Analytics and long-term history | Fast analysis of large files without a heavy database |
| Playwright | The browser engine | Reliable control of tabs, sessions, downloads, and tracing |
| Codex CLI / Claude Code CLI | Background agents | Diagnosis and repair within policy and permissions |
| OpenTelemetry (later) | Metrics and tracing | Once the system grows we need unified measurement |

**A fixed rule:** any smart or visual component (Vision, Camoufox, external
tools) enters as an "optional adapter" behind a contract, never as the
system's foundation. If it were removed, the core keeps working.

---

## 3. The extraction ladder (cheapest and most reliable method first)

```
1) Network/direct download  →  2) DOM via Playwright  →  3) Self-healing paths
   →  4) Vision (images)  →  5) Desktop control
```
We never drop to a more expensive layer unless the cheaper one fails. We
apply the exact same logic to choosing a model in the next section.

---

## 4. Division of labor between the two models

### The "plan" phase — Opus 5 High (me)
- Architecture and component boundaries.
- Contracts (interfaces), data shapes, and the database.
- The workflow engine, locking, resumption, error classification, retry policy.
- Escalation, security, and permission policy.
- Writing the reference tests that define "correct."
- Reviewing any work that touches a contract or schema.

### The "execute" phase — Sonnet 5 Medium
- Implementing an adapter behind an existing contract (Playwright, a file
  validator, an alert sender).
- UI screens and wiring them to ready API endpoints.
- System and report definitions in YAML files.
- Additional tests, cleanup, documentation, error messages.
- Small, scoped fixes.

### Quick decision table

| Question | Answer | Model |
|---|---|---|
| Does the task change a contract or a database table? | Yes | Opus High |
| Is there a new concurrency/resumption/partial-failure case? | Yes | Opus High |
| Is there a security, permission, or escalation decision? | Yes | Opus High |
| Does the contract exist, is the test written, and it's "fill in the body"? | Yes | Sonnet Medium |
| Is it a screen/wiring/definition/cleanup? | Yes | Sonnet Medium |
| Did Sonnet fail twice on the same task? | — | Escalate to Opus High |

---

## 5. Phases and deliverables

| Phase | Deliverable | Owner | Acceptance criterion |
|---|---|---|---|
| P0 — Foundation | Documentation, vision, and rules | Done ✅ | The docs exist |
| **P1 — Core** | Settings, SQLite + migrations, event log, resumable workflow engine, port contracts, HTTP interface | **Opus (done ✅)** | `pytest` is green (18 tests) |
| **P2 — Adapters** | File validator, Playwright adapter, run worker and scheduling, Parquet/DuckDB archiving, system definitions | **Sonnet (done ✅)** | Packets S-01..S-06 are green (97 tests) |
| **P3 — The web app** | Dashboard, runs, events, incidents, live streaming | **Sonnet (done ✅)** | The screens actually work against the current API (a real Playwright test) |
| **P4 — Agent manager** | Running Codex/Claude, analysis-only mode, a full log | **Contract: Opus — Execution: Sonnet (done ✅)** | Running an agent in analyze-only mode writes no file (proven by a test) |
| P5 — Early warning | Baselines, degradation detection, alert levels | Logic: Opus — Rules: Sonnet | **Partial ✅ (F-07):** slowness alerting with fixed thresholds actually works. Remaining: dynamic baselines and gradual trend/degradation detection |
| P6 — Self-healing | Known fixes, sandbox, testing, rollback | Opus | No deployment without a successful test |
| P7 — Vision | An image adapter with relative coordinates | Contract: Opus — Execution: Sonnet | One difficult case succeeds |
| P8 — Linking departments | A dependency graph and chained execution | Opus | A failure's impact shows up on dependent processes |

**Target pilot scope:** 3 systems, 5 reports, one automation project, one
lateness alert, one complete incident, and an agent in analysis-only mode.
**This scope is now complete (P1-P4 done).**

**Deliberately left out of P4:** Experiment and Execute modes — the current
packet only proves that analyze mode writes no file. Enabling the other two
modes is a security decision (sandbox + testing + human approval) that
needs Opus design before any implementation.

**The wiring decision (done ✅):** every safe, local adapter (the file
validator, browser engine, local log, analytical archive, system registry)
is now wired up by default in `Services` — `collect.report` actually works
with no fake adapter. The AI agent is off by default and is only enabled
explicitly in `read_only` mode (D018, D019). Details in
`docs/DECISION_LOG.md` and the tests in `tests/test_services_wiring.py`.

**The F-01..F-12 packet (done ✅):** saved login sessions and session-expiry
detection (D020), failure evidence on disk keyed by run_id (D021), system
definitions with authentication and scheduling, automatic scheduling inside
the background worker (D022), slowness alerting with fixed thresholds
(F-07), a command-line interface (`python -m smartops`), and API/web
endpoints to show session status and trigger on-demand collection. See
`docs/FINISH_PACKET_SONNET.md` for full details. **Not yet run against a
real production system** — that is the actual remaining step before
expanding scope.

---

## 6. Token-saving policy (mandatory)

1. **Contract before code**: Opus writes the interface + the test, and
   Sonnet writes only the body. No random exploration.
2. **Packaged tasks**: every task in `AGENT_TASK_PACKETS.md` states exactly:
   the files allowed to be read, the contract, and one test command. Reading
   the whole repository is forbidden.
3. **No broad sweeps**: no reading every document, no general search. The
   packet has enough context.
4. **Small, single-responsibility files**: the smaller the file, the less context is required.
5. **Batch related tasks into one session** (the same unit) to reuse the
   session's memory instead of re-explaining.
6. **Local edits, never rewrites**: regenerating a whole file to change one line is forbidden.
7. **The test is the stopping point**: the moment it goes green, stop. No
   long reports, no extra explanations.
8. **Escalation only on written conditions** (the table above), never on a hunch.
9. **Docs are updated by adding a line**, not by rewording whole pages.

---

## 7. Definition of "done"

- The tests are green.
- Every meaningful step logs an event.
- Any download is validated before being considered successful.
- Any failure opens an incident with evidence.
- No secrets and no company data in the repository.
- The change is documented in one line in `DECISION_LOG.md` if it touched an architectural decision.

---

## 8. The biggest risks and how we handle them

| Risk | Mitigation |
|---|---|
| Site interfaces change | The extraction ladder + self-healing + caching the new path once verified |
| A file downloads incomplete or outdated | Validation is mandatory: size, opening, date, columns, rows, duplication |
| A run stops midway | Every step is saved; resumption continues from the last success |
| An agent makes a dangerous change | Permission modes + sandbox + testing + human approval for sensitive cases |
| Cost explosion | The escalation ladder from cheapest to most expensive + the token policy above |
