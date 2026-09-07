"""Small, read-only summaries of a recording's own step list.

Kept separate from `coach.py` so the two questions answered here — which
steps are new, and which steps still have no proof they worked — can be
tested as plain arithmetic over a list, with no browser, no thread, and no
CLI agent anywhere nearby. Every function below reads the steps a recording
has already written down and changes nothing; there is nothing here for a
future edit to accidentally turn into an action on the browser, and it should
stay that way.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import RecordingStep

# A caller that asks for everything at once has misread the point of this
# module — following a recording is supposed to stay cheap — so the page size
# is capped rather than trusted, the same way a network endpoint would.
_MAX_STEP_PAGE = 200


def recent_steps(
    steps: list[RecordingStep], *, since_seq: int = 0, limit: int = 50
) -> dict[str, Any]:
    """Only the steps recorded after `since_seq`, in order.

    Watching a long recording by re-reading every step on each question is
    both slow and pointless past the first answer: the caller already has
    everything up to its own cursor. A step's `seq` is that cursor — assigned
    once, never reused, and monotonic even across a pause and resume — so
    "since_seq" names an exact point in the recording with nothing further to
    negotiate.
    """
    limit = max(1, min(int(limit or 50), _MAX_STEP_PAGE))
    later = sorted((step for step in steps if step.seq > since_seq), key=lambda step: step.seq)
    page = later[:limit]
    return {
        "steps": [step.to_dict() for step in page],
        "since_seq": since_seq,
        "next_since_seq": page[-1].seq if page else since_seq,
        "more_available": len(later) > len(page),
        "total_steps": len(steps),
    }


def thin_evidence(steps: list[RecordingStep]) -> list[dict[str, Any]]:
    """Steps whose recorded proof is still `{"type": "none"}`.

    Not a verdict on the recording — a click's proof is routinely filled in
    later, once a reviewer has seen what it actually revealed, and several of
    the platform's own step types (a hover, a drag, a key press) are recorded
    this way on purpose because what they prove is page-specific. This is a
    pointer at exactly the moments worth a second look while the recording is
    still running and the person can simply repeat one of them more
    carefully, instead of finding out during a replay that nothing was ever
    checked.
    """
    return [
        {
            "seq": step.seq,
            "action": step.action or step.kind,
            "page": (step.target or {}).get("page", "main"),
            "target_text_redacted": step.target_text_redacted,
        }
        for step in steps
        if (step.success or {}).get("type", "none") == "none"
    ]
