"""Extraction engine contract. Layers: network -> DOM -> self-healing -> vision -> desktop.

Any real implementation (Playwright or otherwise) follows only this contract,
so the core stays independent.

Additional filters keys (F-02):
- logged_in_selector: an element that must be present after sign-in; its
  absence means the session expired.
- login_selector: an element that must be absent after sign-in (such as the
  login form); its presence means the session expired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..domain.enums import ExtractionLayer


@dataclass(frozen=True)
class ExtractionRequest:
    system: str
    report: str
    destination_dir: Path
    period: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    allowed_layers: tuple[ExtractionLayer, ...] = (
        ExtractionLayer.NETWORK,
        ExtractionLayer.DOM,
        ExtractionLayer.SELF_HEALING,
    )
    timeout_seconds: float = 300.0
    # F-02: the run id (to tie evidence to one specific run), the path to a
    # saved session (storage_state) to reuse instead of signing in from
    # scratch, and the evidence directory.
    run_id: str = ""
    session_state_path: Path | None = None
    evidence_dir: Path | None = None


@dataclass
class ExtractionResult:
    ok: bool
    layer_used: ExtractionLayer
    file_path: Path | None = None
    original_name: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    # True when the failure was an expired or missing session, not an ordinary error.
    auth_required: bool = False


class BrowserPort(Protocol):
    """Execute one extraction request and return the resulting file plus evidence of what happened."""

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...

    def capture_evidence(self, run_id: str) -> dict[str, Any]:
        """Screenshot/trace/network evidence for one failed run, used to build the incident pack."""
        ...
