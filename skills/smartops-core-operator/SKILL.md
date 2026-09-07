---
name: smartops-core-operator
description: Use whenever building, debugging, or operating this repository's one daily Chrome download flow so confirmed lessons are reused and the scope stays small.
---

# SmartOps core operator

## Start every task

1. Read `AGENTS.md`, `README.md`, and `docs/DAILY_DOWNLOAD_FLOW.md`.
2. If `logs/operator-memory.md` exists, read it before investigating. It is the
   current generated view of confirmed lessons; `logs/operator-learning.jsonl`
   is the append-only history.
3. Work only on the active daily download flow. Use the existing Chrome,
   recording, replay, storage, and validation path.

## Evidence order

Use Network/API, then DOM, then Vision, then Desktop. The SmartOps Tab Bridge is
read-only DOM evidence. A screenshot is read-only visual evidence. Neither is a
second action engine; the existing replay path performs browser actions.

Samsung capture protection can make an otherwise live G-MES window appear as a
gray `No Picture Taking!` frame to desktop screenshots. Treat that as an
evidence limitation, not a failed page: inspect sanitized DOM structure,
Playwright actionability, and network/download events. The SmartOps server must
run on the interactive Windows desktop; a server started in the restricted
agent desktop cannot launch the recorder (`WinError 5`).

## Confirmed daily G-MES path

For `Production Plan by Order(Line)`, keep this order: open the screen, select
the `VD` organization checkbox, set and commit both dates, click the fixed
bottom-left `Inquiry` button, wait for the result grid to finish loading, click
the top Excel icon, then click `OK` in `PopupExcelExport`. A panel scroll and
modifier-only keypresses are recorder noise, not business actions.

Opening the screen may show the exact informational message `Selecting an org
chart before adding row(s)`. Dismiss only that exact message with its own
`Info_1.form.btnOk` control, then continue to `VD`; never auto-confirm another
dialog.

## Learn without polluting memory

Add a lesson through `POST /api/learnings` only after all four facts are known:
problem, confirmed cause, implemented solution, and actual verification. Never
promote a guess, secret, credential, cookie, company data, raw snapshot, or
download content.

Every accepted lesson automatically appends the private JSONL history and
regenerates `logs/operator-memory.md`; do not maintain a competing notes file.

When a new Samsung G-MES UI path is proven, also update and validate the
external `samsung-gmes-automation` skill as required by the machine rules.
