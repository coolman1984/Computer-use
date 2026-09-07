# AGENTS.md

These instructions are mandatory for any software agent working on this repository.

## Understand before you modify

Read in order:
1. README.md
2. docs/DAILY_DOWNLOAD_FLOW.md
3. skills/smartops-core-operator/SKILL.md and, when present, logs/operator-memory.md
4. The source and focused tests for the behaviour being changed

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
