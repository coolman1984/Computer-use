# AGENTS.md

These instructions are mandatory for any software agent working on this repository.

## Understand before you modify

Read in order:
1. README.md
2. PROJECT_EYE.md — the project from above: its domains, what runs, which way
   dependencies may point, the journeys that matter, and the red zones that are
   known and recorded rather than hidden.
3. docs/ARCHITECTURE_MAP.md — what each module owns, and the rules a new one
   must fit. Adding a module without reading this is how the same job ends up
   owned twice.
4. docs/DAILY_DOWNLOAD_FLOW.md
5. skills/smartops-core-operator/SKILL.md and, when present, logs/operator-memory.md
6. The source and focused tests for the behaviour being changed

## Before you change anything

Say these out loud before the first edit. If one of them is unknown, find out
rather than guessing — a change whose blast radius nobody worked out is how a
project stops being understandable.

- **What is the target**, and which domain owns it (PROJECT_EYE.md §2).
- **Which journey** it serves, and what proves that journey today (§5).
- **What depends on it**: `python scripts/project_eye.py` prints the graph, and
  the `depended_on_by` count in `.project-eye/graph.yaml` is the honest answer.
- **How far the change reaches** — one function, one module, one domain, or a
  shared contract. The wider it reaches, the more review it needs, and a change
  to `RecordingStep`, `domain/enums.py`, or a plan's shape reaches everything.
- **Which files you are allowed to touch**, and which you are not.
- **Which test fails first** if you are wrong.

Two rules that follow from it:

- **A problem found outside the task is recorded, not fixed.** Say it in the
  report and leave it, unless it blocks the task or risks data loss. Silent
  scope growth is how a small change becomes unreviewable.
- **A fix needs a failing test first.** Reproduce it, add the smallest test
  that fails for the right reason, then fix it. If you could not reproduce it,
  you have not fixed it — say so.

## Build rules

- The primary delivery is one simple, complete daily download flow: run the
  captured task in the configured Chrome profile, save one file, identify its
  real type from its bytes, validate it, and record the outcome.
- Reuse the existing SmartOps browser, replay, storage, and validation path.
  Do not add another framework, browser, profile, service, or parallel
  automation solution unless the existing path demonstrably cannot perform the
  single flow.
- Learn the human path step by step only when real replay evidence requires
  it. Keep recordings, screenshots, traces, sessions, and downloads private.
- Reuse the configured Chrome profile and its required extensions. Screenshot
  tools are read-only evidence; DOM/browser replay performs actions.
- The download name and extension are untrusted. Save completed bytes first;
  determine the actual format from the file structure; add a conventional
  extension only after that proof; then validate content, size, rows, and any
  configured columns before calling the run successful.
- Every meaningful action, saved file, validation verdict, and failure must
  leave a local event. A failed flow can be inspected and resumed safely.
- Never rely on absolute screen coordinates. Use Network/API first, then DOM,
  then Vision, then Desktop.
- One explicit manual evidence replay may exercise a newly completed capture
  even if intermediate UI proof is incomplete. It must stay manual and cannot
  approve, schedule, or automatically retry the flow. Approval and scheduling
  require the normal reviewed-plan gate and a validated run.
- Keep only the minimum private runtime evidence needed for the active flow and
  the minimum synthetic fixtures needed for focused tests. Never store secrets
  or sensitive company data in the repository.

## Verification and completion

- After a change that adds, moves or removes a module, run
  `python scripts/project_eye.py` and commit the regenerated graph. The suite
  fails when the map has drifted from the code, because a map people trust and
  that is wrong is worse than no map.
- Run focused tests for the behaviour changed. Do not run broad suites during
  human training or one-flow work unless the change crosses those boundaries.
- A real manual run is separate evidence: report its actual validation result,
  never infer success from a click or a filename.
- Document only the current single-flow operating model; remove superseded
  plans instead of maintaining competing approaches.
- State known risks. Never claim something works if it has not been tested.
- Keep compatibility with Windows and local operation as much as possible.

## Agent philosophy

An agent is an organized assistant inside the platform, not an owner of absolute authority.
