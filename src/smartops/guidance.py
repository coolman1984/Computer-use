"""Turn a platform failure into something a non-technical person can act on.

Every failure reaches the user through here, so the answer to "what happened
and what do I do now" is written once and stays consistent whether it surfaces
on a run page, an issue, or a stage that refuses to advance.

Three things, always, and never more: what happened, why it happened, and the
single next click. A message without an action is a dead end, and a dead end is
what sends a non-technical user back to the terminal.
"""

from __future__ import annotations

from typing import Any

# error_class -> (what happened, what to do, button label, page)
_BY_ERROR_CLASS: dict[str, tuple[str, str, str, str]] = {
    "auth": (
        "The saved sign-in for this system has expired.",
        "Sign in again once; the automation then continues on its own.",
        "Sign in again",
        "credentials.html",
    ),
    "target_not_found": (
        "A button or field the recording relied on is no longer on the page.",
        "The site has changed. Record the task again so the platform learns the new layout.",
        "Record again",
        "recordings.html",
    ),
    "data_quality": (
        "The file downloaded, but it did not pass the checks — it may be empty, "
        "the wrong type, or a repeat of a file already collected.",
        "Open the file's details to see which check failed, then run it again once "
        "the source system has the right data.",
        "Open Files",
        "files.html",
    ),
    "rate_limit": (
        "The site asked the platform to slow down.",
        "Nothing to do — it retries by itself. If it keeps happening, run this "
        "automation less often.",
        "Open Automations",
        "processes.html",
    ),
    "transient": (
        "Something on the site or the network did not respond in time.",
        "This usually fixes itself and is retried automatically. If it keeps failing, "
        "check the site opens normally and that the VPN is connected.",
        "Test the connection",
        "systems.html",
    ),
    "permanent": (
        "Something in the setup is wrong, so retrying would not help.",
        "Check the system's details — the addresses and the markers — then test the "
        "connection again.",
        "Open Systems",
        "systems.html",
    ),
    "internal": (
        "The platform itself hit an unexpected problem.",
        "Run the health check. If it keeps happening, this one needs a technical look.",
        "Run the health check",
        "index.html",
    ),
}

_UNKNOWN = (
    "The run stopped for a reason the platform could not classify.",
    "Open the run to see the exact step it stopped on.",
    "Open Runs",
    "runs.html",
)


def explain(
    error_class: str | None, *, message: str = "", details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The user-facing explanation of one refusal or failure.

    `details` may carry a more specific fix than the error class implies — a
    blocked stage knows exactly which page unblocks it — and that always wins
    over the generic advice.
    """
    details = details or {}
    # A blocked stage is not a fault: nothing is broken, an earlier step simply
    # has not happened yet. Saying "something in the setup is wrong" there would
    # send the user hunting for a problem that does not exist.
    if details.get("blocked_stage"):
        fix = details.get("fix") or {}
        return {
            "what_happened": "This step needs an earlier one finished first.",
            "what_to_do": message,
            "action": {
                "label": fix.get("label") or "Go back a step",
                "href": fix.get("href") or "index.html",
            },
            "technical_detail": "",
        }

    what, todo, label, href = _BY_ERROR_CLASS.get(error_class or "", _UNKNOWN)
    fix = details.get("fix") or {}
    if fix.get("label") and fix.get("href"):
        label, href = fix["label"], fix["href"]
    return {
        "what_happened": what,
        "what_to_do": todo,
        "action": {"label": label, "href": href},
        # The raw message is kept but clearly separated: useful when a technical
        # person is asked to look, never the first thing the user reads.
        "technical_detail": message,
    }


def explain_run(run: Any) -> dict[str, Any] | None:
    """Explanation for a failed run, or None when the run did not fail."""
    if not run.error_class:
        return None
    return explain(run.error_class, message=run.error_message or "")
