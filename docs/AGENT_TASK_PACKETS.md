# Ready-Made Task Packets (Sonnet 5 Medium Execution)

Every packet here is **self-contained**. The goal is that the executing model does not need to read the whole repository.

## Mandatory rules for any packet

1. Read only the files listed under "Read". General repository search is forbidden.
2. Never modify core files: `core/`, `domain/`, `storage/db.py`, `engine/`, `ports/`.
   If you are forced to modify them → stop and escalate to Opus.
3. Output = code + a test. Stopping point = the test is green.
4. Local edits only, never rewriting whole files.
5. New adapters go under `src/smartops/adapters/<domain>/`.
6. Wiring in `services.py` is one line or via a setting, with no change to the container's structure.

## Template for any new task

```
Title | The goal in one sentence | Files allowed to be read | The contract to implement
| Files to create | Acceptance criterion (one test command) | Prohibitions
```

---

## S-01 — Local file validator (done ✅)
- **Goal:** implement `FileValidatorPort` so a corrupt, incomplete, or duplicate file is never accepted.
- **Read:** `src/smartops/ports/validation.py`, `tests/test_collect_workflow.py`.
- **Create:** `src/smartops/adapters/validation/local.py` + `tests/test_validation_adapter.py`.
- **Required:** check existence, size, and extension, compute sha256, open CSV/Excel and read columns and row count, check the file's age, and detect duplicates via `services.files.find_by_hash`.
- **Acceptance:** `pytest tests/test_validation_adapter.py -q` is green, covering: a valid file, an empty file, a wrong extension, a missing column, a duplicate file.
- **Forbidden:** changing `ValidationReport` or `ValidationRules`.

## S-02 — Browser adapter (Playwright) (done ✅)
- **Goal:** implement `BrowserPort` with a "network" layer then a "DOM" layer.
- **Read:** `src/smartops/ports/browser.py`, `tests/test_collect_workflow.py` (the fake browser is the behavioral reference).
- **Create:** `src/smartops/adapters/browser/playwright_engine.py` + `tests/test_browser_adapter.py` (test against a local page, never a real site).
- **Required:** an isolated context, viewport from settings, waiting for the download and saving it to `destination_dir`, recording which layer was used, and collecting evidence (a screenshot + trace) on failure, and raising `TransientError` for temporary errors and `AuthError` for an expired session.
- **Acceptance:** `pytest tests/test_browser_adapter.py -q` is green.
- **Forbidden:** absolute screen coordinates, or storing any login data in code.

## S-03 — System and report definitions (done ✅)
- **Goal:** load `config/systems/*.yaml` files and turn them into `collect.report` runs.
- **Read:** `src/smartops/config.py`, `src/smartops/workflows/builtin.py`.
- **Create:** `src/smartops/workflows/profiles.py` + `config/systems/example.yaml` + `tests/test_profiles.py`.
- **Required:** for each system: the name, reports, validation rules, normal duration, alert rules. Validate the definition and raise `ConfigurationError` with a clear message if incomplete.
- **Acceptance:** `pytest tests/test_profiles.py -q` is green, and an incomplete definition gives an understandable message.

## S-04 — Worker and scheduling (done ✅)
- **Goal:** run due runs automatically instead of a manual call.
- **Read:** `src/smartops/engine/runner.py` (only the `execute` and `drive` functions), `src/smartops/storage/repositories.py` (`RunRepository.due`).
- **Create:** `src/smartops/worker.py` + `tests/test_worker.py`.
- **Required:** a loop that reads `runs.due()` and executes while respecting `browser.max_concurrency`, with a clean stop, and no overlap (the lock already exists in the repository — do not reinvent it).
- **Acceptance:** `pytest tests/test_worker.py -q` is green, and proves two runs never overlap.

## S-05 — Live event streaming (done ✅)
- **Goal:** a WebSocket endpoint that streams events to the UI live.
- **Read:** `src/smartops/events/bus.py`, `src/smartops/api/app.py`.
- **Create:** `src/smartops/api/ws.py` + `tests/test_ws.py`.
- **Required:** `/ws/events` subscribes to `services.bus`, unsubscribes on disconnect, and supports filtering by `run_id`.
- **Acceptance:** `pytest tests/test_ws.py -q` is green (a connection receives at least one event).

## S-06 — History archiving (Parquet + DuckDB) (done ✅)
- **Goal:** turn validated files into analytical history.
- **Read:** `src/smartops/storage/paths.py`, `src/smartops/domain/models.py` (`FileArtifact` only).
- **Create:** `src/smartops/adapters/history/archiver.py` + `tests/test_archiver.py`.
- **Required:** write Parquet partitioned by date, system, and report, and a simple DuckDB query function that compares two periods.
- **Acceptance:** `pytest tests/test_archiver.py -q` is green.
- **Forbidden:** uploading any real data to the repository.

## S-07 — The web app (done ✅)
- **Goal:** an operations center for a non-technical user.
- **Read:** `src/smartops/api/app.py` only (the API endpoints are the contract).
- **Create:** `web/` (static pages or Vite) + wire it to the FastAPI service.
- **Required:** a status dashboard, a run list, run details with its steps and events, incidents, files, and a run/retry button. Clear and free of technical jargon in the UI text.
- **Acceptance:** the screens work against `platform.selfcheck` data with no backend modification.

## S-08 — Agent manager (analysis-only mode) (done ✅)
- **Goal:** run Codex/Claude CLI and log everything.
- **Read:** `src/smartops/ports/agents.py`, `src/smartops/storage/repositories.py` (`AgentRunRepository` only).
- **Create:** `src/smartops/adapters/agents/cli_runner.py` + `tests/test_agent_runner.py` (with a fake process, no real invocation).
- **Required:** implement `AgentRunnerPort`, stream output, a timeout, token logging, and enforcing `AgentMode.ANALYZE` (no file modification is permitted in this mode).
- **Acceptance:** `pytest tests/test_agent_runner.py -q` is green, and proves analyze mode writes no file.
- **Forbidden:** enabling execute or experiment mode in this packet.

## S-09 — Alert channels (done ✅)
- **Goal:** implement `NotifierPort` (a local log + an optional webhook).
- **Read:** `src/smartops/ports/notify.py`.
- **Create:** `src/smartops/adapters/notify/local.py` + `tests/test_notifier.py`.
- **Acceptance:** `pytest tests/test_notifier.py -q` is green.

## S-10 — The incident pack (done ✅)
- **Goal:** gather evidence into one folder whenever an incident opens.
- **Read:** `src/smartops/domain/models.py` (`Incident`), `src/smartops/storage/repositories.py` (`IncidentRepository`).
- **Create:** `src/smartops/adapters/incidents/pack.py` + `tests/test_incident_pack.py`.
- **Required:** a summary, the error, the steps, the events, expected vs. actual files, and similar incidents via `find_by_signature`, with the path written to `incident.pack_path`.
- **Acceptance:** `pytest tests/test_incident_pack.py -q` is green.

---

## Recommended order (fully executed ✅)

`S-01 → S-02 → S-03 → S-04` (the collection core actually works) then `S-10 → S-09 → S-05 → S-07` (visibility and control) then `S-06 → S-08`.
All ten packets are implemented and tested — see `docs/EXECUTION_PLAN.md` section 5 for each phase's status, and the "Next step" section below for what remains.

**F-01 → F-12 (done ✅):** the full real-run packet (sessions, scheduling,
slowness alerting, the CLI, a systems/alerts UI) was implemented in one pass
per `docs/FINISH_PACKET_SONNET.md`. Every binding detail (the fields, the
session-expiry detection logic, the due-ness logic) is documented there.

## Next step: wiring, not new packets

Every packet built an independent adapter behind its contract (ports/)
without automatically wiring it into `services.py` — by design, so the core
stays independent of any specific adapter. What remains is not a new Sonnet
packet but a **deliberate wiring decision made by Opus**: which adapters are
actually enabled in `main.py`/production (a real Playwright? a real webhook?
which agent model?) — this is an operational/security decision that needs
the same level of architectural care as the original design, so it is not
left to Sonnet alone.

## When to stop Sonnet and escalate

- The task needs a contract or database schema change.
- A new concurrency or resumption case appeared.
- A test failed twice for the same reason.
- A decision concerns security, permissions, or data deletion.
