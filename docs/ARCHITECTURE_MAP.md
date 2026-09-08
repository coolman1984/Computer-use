# What is where, and what talks to what

One page for the question "if I change this, what else moves?". Read it before
adding a module; the honest answer is usually that something here already owns
the job.

## The shape in one picture

```
                        a person demonstrates the task once
                                        │
        ┌───────────────────────────────▼───────────────────────────────┐
        │                          RECORDING                            │
        │                                                               │
        │  recordings/worker.py ── drives headed Chrome, owns capture   │
        │        │                                                      │
        │        ├─ vision.py ......... frames, measured before trusted │
        │        ├─ elements.py ....... one description per control     │
        │        ├─ timeline.py ....... one observation per action      │
        │        ├─ confidence.py ..... which steps are weak, now       │
        │        ├─ probe.py .......... what this screen can offer      │
        │        └─ redaction.py ...... nothing sensitive gets past     │
        │                                                               │
        │  recordings/manager.py ── lifecycle, persistence, undo        │
        └───────────────────────────────┬───────────────────────────────┘
                                        │  steps + elements
        ┌───────────────────────────────▼───────────────────────────────┐
        │        COMPILING          recordings/converter.py             │
        │  steps ─► plan: locators, proofs, retry policy, review gate   │
        └───────────────────────────────┬───────────────────────────────┘
                                        │  reviewed, approved plan
        ┌───────────────────────────────▼───────────────────────────────┐
        │         REPLAY            adapters/browser/replay.py          │
        │  deterministic: no model in the loop, fails rather than guess │
        └───────────────────────────────┬───────────────────────────────┘
                                        │  the file it produced
        ┌───────────────────────────────▼───────────────────────────────┐
        │       VALIDATION      adapters/validation/local.py            │
        │  real type from the bytes, then size, rows, columns           │
        └───────────────────────────────────────────────────────────────┘
```

Everything else — the web app, the scheduler, the runner — surrounds that line
rather than sitting on it.

## Who owns what

### Capture — `src/smartops/recordings/`

| Module | Owns | Depended on by |
| --- | --- | --- |
| `worker.py` | The headed browser and the in-page capture script. The only place that watches a person work. | `manager.py` |
| `vision.py` | Taking a frame and **measuring** it. Blank detection, the renderer escalation, `observe_page`. | `worker.py`, `probe.py` |
| `elements.py` | One description per control, shared across steps and recordings. Repair once, every step follows. | `worker.py`, `manager.py`, `replay.py`, the API |
| `timeline.py` | One observation per action: before, after, what changed, what could prove it. | `worker.py` |
| `confidence.py` | Whether a step was identified and observed well enough to trust. | `worker.py`, `converter.py` |
| `probe.py` | What a screen offers before a recording is spent on it. Nexacro, accessibility, markup, pixels. | `worker.py`, `cli.py` |
| `converter.py` | Turning steps into an executable plan, and the review gate. | `manager.py` |
| `instruments.py` | Read-only summaries of a recording for an assistant to follow. | `coach.py`, the API |
| `coach.py` | The assistant's read-only view of a live recording. | the API |
| `manager.py` | Lifecycle: create, pause, undo, finish, merge elements, draft. | the web app |
| `redaction.py` | The boundary. Nothing reaches a step, a plan, or a screen without passing it. | everything above |

### Execution — `src/smartops/adapters/browser/`

| Module | Owns |
| --- | --- |
| `replay.py` | Performing a plan and proving each step. The only code that acts on a browser during a run. |
| `session.py` | Opening Chrome the same way for every purpose, and the single-owner profile guard. |
| `authentication.py` | Getting signed in, before any capture exists. |
| `playwright_engine.py` | Wiring a request to a session: tracing, evidence, the element repository. |

### The shared vocabularies — `src/smartops/domain/enums.py`

Two sets live here because more than one module has to agree about them, and
each had already drifted once when they did not:

* `ACTIONS_WITHOUT_AN_ELEMENT` — which actions need no locator.
* `PROVABLE_SUCCESS_TYPES` — every proof the replay engine can check.

A test fails if the second one ever disagrees with the engine again.

## The rules a new module has to fit

1. **One engine acts.** Sensors observe; `replay.py` and `worker.py` are the
   only things that drive a browser. A second one is a race, not redundancy.
2. **Fail rather than guess.** Ambiguity stops the run. This is why the
   fingerprint *names* a replacement and never presses it.
3. **The step contract is fixed.** `RecordingStep` is built with
   `RecordingStep(**data)`, so a new top-level key breaks every recording. New
   facts go in `inputs` under a leading underscore — that is what
   `_observed_visible_before`, `_observed_sizes_before`, `_fingerprint` and
   `_element` all are — or into a sidecar file beside the recording.
4. **Sidecars, not schema changes.** `timeline.jsonl`, `elements.json`,
   `screenshots/quality.jsonl` and `vision-summary.json` all sit next to
   `steps.jsonl` rather than inside it.
5. **Redaction is not optional.** Anything that leaves the browser goes through
   `redaction.py`. Counts and lengths are allowed; contents are not.
6. **No absolute screen coordinates**, ever. A fraction of the window or of a
   known element, or it is not recorded.
7. **Backwards compatible by construction.** A recording made before a feature
   existed must still replay. The element repository is the pattern: consulted
   first when present, silently absent otherwise.

## Where a recording's evidence lives

```
<artifact_dir>/
├── steps.jsonl ................ the contract: what replay executes
├── elements.json .............. every control this recording touched
├── timeline.jsonl ............. one observation per action
├── screenshots/
│   ├── 000001.png ............. frames, measured
│   ├── quality.jsonl .......... the verdict on each one
│   └── vision-summary.json .... what this recording's pictures are worth
├── downloads/ ................. the files the task produced
├── network/sanitized-summary.json
├── session/ ................... storage state, auth diagnostics
└── trace/trace.zip ............ Playwright's own record
```

Per system, outside any recording:

```
<recordings_dir>/elements/<system>.json   the shared element repository
```

None of it is ever committed. This repository is public.

## Reaching it from outside

| Path | Answers |
| --- | --- |
| `GET /api/recordings/{id}/live` | What is on screen in the recorder right now |
| `GET /api/recordings/{id}/vision` | Why the screenshots look the way they do |
| `GET /api/recordings/{id}/capabilities` | What this screen offers an automation |
| `GET /api/recordings/{id}/recent-steps` | What has been captured since a cursor |
| `GET /api/recordings/{id}/thin-evidence` | Which steps nothing yet proves |
| `POST /api/recordings/{id}/undo-last-step` | Take back a mis-click, still recording |
| `GET /api/systems/{key}/elements` | What this system's automations depend on |
| `PUT /api/systems/{key}/elements/{ref}` | Repair one control, for every step |
| `python -m smartops probe <system>` | The same capability report, from a terminal |
