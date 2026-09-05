# AGENTS.md

These instructions are mandatory for any software agent working on this repository.

## Understand before you modify

Read in order:
1. README.md
2. docs/PROJECT_CONTEXT.md
3. docs/ARCHITECTURE.md
4. docs/BROWSER_EXTRACTION_ENGINE.md
5. docs/AI_AGENT_ORCHESTRATION.md
6. docs/OBSERVABILITY_SELF_HEALING.md
7. The specialized document tied to the task

## Build rules

- Keep the core simple.
- Do not add a new technology if what exists already solves the problem.
- Every workflow must be resumable after a failure.
- Every meaningful step must leave an event in the log.
- Any download must be validated before it is considered successful.
- Any self-modification must go through testing and rollback.
- Separate the test environment from production.
- Never rely on absolute screen coordinates.
- Use Network/API first, then DOM, then Vision, then Desktop.
- Never store secrets in the repository.
- Never add sensitive company data.

## Before finishing any task

- Run the tests.
- Document what changed.
- State known risks.
- Never claim something works if it hasn't been tested.
- Keep compatibility with Windows and local operation as much as possible.

## Agent philosophy

An agent is an organized assistant inside the platform, not an owner of absolute authority.
