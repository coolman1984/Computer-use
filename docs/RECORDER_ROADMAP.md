# Recorder roadmap

How the recorder gets from where it is to a capture nobody has to babysit,
in the order the work actually has to happen.

This changes no business scope. The goal stays the one in
[`tasks/CURRENT.md`](../tasks/CURRENT.md): one G-MES daily report, downloaded,
proved from its bytes, validated, recorded. What this document sequences is the
*capture* side of that flow, because that is what keeps failing.

## The idea in one line

One recording engine, many senses, and a strategy that picks the strongest
sense a given screen actually offers.

Everything below follows from one observation: every attempt so far leaned on
whichever sensor was tried first — a CSS selector, or a screenshot — and a
corporate portal defeats each of them in turn. Ids are generated. The screen is
painted rather than built. The window's picture is withheld. The grid is not a
table. None of that is a reason to stop; each is a reason not to have bet
everything on one sense.

So the recorder does not get *more* automation. It gets more ways to see, one
place that puts what it saw in order, and an honest refusal when it saw nothing.

## The order, and why it is this order

Each phase is worth shipping alone, and each one exists because the phase after
it cannot be judged without it.

### Phase 1 — See honestly · **done**

Nothing can be built on evidence that lies. A screenshot that returned a flat
grey rectangle used to be indistinguishable from a good one.

* `recordings/vision.py` — frames come through the DevTools protocol and are
  measured before they are believed. A flat frame is re-taken by a route that
  never reads the window's surface; a page that needs that route keeps it. The
  verdict is exact (a tiny probe decoded by a stdlib PNG reader) and reported
  per region, so a grey panel over a working page is not confused with a dead
  window. A blank frame is still saved — and recorded as blank.
* `observe_page()` — a screen described in facts an agent can act on when there
  are no pixels at all, including whether it is painted rather than built.
* Every frame is indexed in `screenshots/quality.jsonl`; the recording ends with
  `screenshots/vision-summary.json`.

**Proved by** `tests/test_page_vision.py`, against real Chrome on pages that are
blank, partly blank, drawn, and ordinary.

### Phase 2 — Know what a screen offers · **done**

A recording spent on a screen that nothing can identify is a wasted afternoon,
and we only found that out afterwards.

* `recordings/probe.py` — asks one open screen every question that decides how
  it can be automated: is there a Nexacro object model behind it (named forms
  and components, grids bound to datasets), can Chrome name its controls, do its
  markup ids survive a reload, can anything see the window. It answers with the
  identity and the proof to build steps on, and says plainly when a screen
  carries neither.
* `smartops probe <system>` for the real screen, with the real profile; live
  during a recording at `/api/recordings/{id}/capabilities`.

**Proved by** `tests/test_capability_probe.py`, including a Nexacro-shaped screen
whose markup is deliberately useless — it scores well only through the object
model.

**Why the dataset matters more than the picture.** A bound grid's row count going
from 0 to 758 is direct proof that a query ran. It costs nothing, it cannot be
faked by a click, and it survives a machine that refuses to show the browser at
all. That is the single most valuable thing the probe looks for.

### Phase 3 — Run the probe on the real screen · **next, and it needs you present**

Everything after this is shaped by what the answer is, so it comes before more
code, not after it.

Open G-MES on the working report screen and run `smartops probe gmes --wait 60`.
The report says which sensors are real there. Concretely, the three answers that
change the plan:

* **Nexacro answers and its grids are bound** → Phase 5 becomes the highest
  value work in the project, and screenshots stop mattering much.
* **Nexacro is absent or its application is unreachable** → Phase 4 and the
  accessibility sensor carry the weight instead.
* **No route can see the window** → confirmed, written down, and worked around
  rather than fought. We do not attempt to defeat the company's capture
  protection.

**Stop condition:** if the probe reports no usable identity, do not record that
screen. Find the screen that does.

### Phase 4 — One timeline instead of five subsystems

Today each sense keeps its own notes: steps in the database, frames on disk,
network in a summary file, tabs in the worker's memory. An assistant asked "what
happened after the last click" has to reassemble that from four places, and a
step's proof is chosen from whichever fragment happened to be at hand.

Build one record per action — what was done, where, what the page looked like
before and after, what changed, what could prove it — and have every sense write
into it. Nothing new is captured in this phase. It is the same evidence, put in
order, which is what makes the two phases after it possible at all.

**Done when** one action's full before/after is one object, and the recorder's
existing steps are built from it rather than beside it.

### Phase 5 — Ask the application, not the page

Only if Phase 3 says Nexacro is really there.

The probe already reads the object model once. This phase reads it around every
action: which component was pressed, by its own name inside the application; and
what its datasets did afterwards. A step then says *"pressed the component
called btnInquiry; the grid's dataset went from 0 rows to 758"* instead of
*"clicked at 71% of a div"*.

This is the phase that would have prevented the failure that started all of
this — a report requested with no organisation selected, which every sensor we
had called a success.

**Done when** a recorded step carries a component identity and, where a grid is
involved, a row-count change as its proof.

### Phase 6 — Prove the download from the export that made it

A file arriving is good evidence. A file arriving *because the export we watched
start finished* is better, and it is what separates the real report from a login
page saved under an Excel name.

Correlate the export the application began, the browser's download, and the
validator's verdict on the bytes. The validation half already exists and works;
this connects it to its cause.

### Phase 7 — Give the assistant real instruments

The current Recording Coach receives no page, no timeline, no state — it offers
generic advice at the start and the interface then calls it "watching". It is
not watching.

Replace the advice with read-only instruments over the Phase 4 timeline: what
just happened, what changed, what the current screen offers, where the proof is
thin. Send differences, not full snapshots, so following a long recording stays
cheap. `/api/recordings/{id}/live` is the first of these and already works.

**The line that does not move:** during a human recording the assistant reads and
explains. It does not click. You have the hands; it has the memory and the
arithmetic.

### Phase 8 — Say which steps are weak, while they can still be redone

Score each step on how well it was identified, how observable its effect was,
and how safely it can be repeated. Say so during the recording — *"that step is
weak, please do it again more slowly"* — instead of discovering it forty minutes
into a replay.

This is the phase that pays back the previous four, and it cannot be built
before them: a score is only worth having once there are several senses to
disagree.

### Phase 9 — Break every sensor on purpose

A local site that is deliberately hostile: generated ids, a popup login, a grid
that fills late, an element replaced on mousedown, a download with no extension,
an HTML login page named `.xlsx`, a screen that cannot be photographed. Some of
these fixtures exist already.

The test is not that the recorder succeeds. It is that when a sensor dies, the
recorder notices and uses the next one — and when none is left, it says so
rather than guessing.

### Phase 10 — The controlled corporate run

One real run, with the operator present, per `tasks/CURRENT.md`. It stops at the
first failure and is diagnosed from sanitized evidence. Three consecutive clean
unattended runs before anything is approved or scheduled.

## What is deliberately not in this plan

Native Windows UI automation, remote-desktop fallbacks, OCR, visual grounding,
and network-level replay are all real techniques and none of them is needed to
find out whether the first flow works. They stay out until it does.

The same goes for the business side, unchanged from `tasks/CURRENT.md`:
no multi-system orchestration, no department linking, no self-healing, no
analytics.

## The rules the phases do not get to break

* **One engine acts.** Sensors observe; Playwright drives. Two things moving the
  same browser is not redundancy, it is a race.
* **Replay is deterministic.** The assistant helps compile the plan. It does not
  execute it.
* **Fail rather than guess.** A missing or ambiguous target stops the run.
* **No absolute screen coordinates**, ever. A press is a fraction of a known
  element or it is not a press.
* **Nothing private leaves the machine**, and nothing sensitive enters the
  repository — no company URLs, credentials, sessions, recordings, screenshots,
  traces, or downloaded reports.
* **A click is not a result.** A step is proved by what the page did, and a run
  is proved by a validated file.
