# Project Context

## Why does this project exist?

The goal is to eliminate the slow, manual work of reaching many reports
across multiple web systems, downloading the raw data, then running
interconnected automation and analysis projects.

The project grew from the idea of adding a browser into a complete
operating platform for automation and digital maintenance.

## Real problems any agent must understand

- There are many systems and many tabs.
- Some pages can be read from the page structure.
- Some pages do not expose their components programmatically.
- Some systems may need to be handled through image and vision.
- Screen resolutions differ.
- Some reports' files can be reached from a network request instead of a manual click.
- Some pages open new windows or tabs.
- Login sessions can expire.
- File and report names can change.
- Downloaded files can be incomplete, outdated, or duplicated.
- The process must remain reviewable and reversible.

## The big goal

The platform becomes one center for managing:

1. Collecting raw data.
2. Running each department's automation.
3. Linking departments and their dependencies.
4. Monitoring authorized sites and services.
5. Proactively detecting slowness and problems.
6. Running AI agents for diagnosis and repair.
7. Keeping a history of every run and every change.
8. Learning from past problems and successful fixes.

## Execution philosophy

The default order for any browser task:

1. Direct download or data request if possible.
2. Page elements via Playwright.
3. A smart layer for changing pages.
4. Visual vision.
5. Full UI control as a last resort.

Do not use AI if a deterministic, fixed solution already works.

## The end user

The end user is non-technical. The web app must be the primary operations
center, not the terminal or code files.

## AI

Codex CLI and Claude Code CLI work as background workers under a central agent manager.

Agents hold no absolute authority. There are permission levels, testing,
rollback, and human approval for sensitive operations.

## Security constraints

- No bypassing company policy or user permissions.
- No storing secrets in the repository.
- No uploading sensitive raw data to a public repository.
- Any sensitive fix requires approval.
