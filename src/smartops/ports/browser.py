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


@dataclass(frozen=True)
class ReplayRequest:
    """Replay a recorded plan and capture whatever file it produces.

    Deliberately the same shape of answer as ExtractionRequest (it returns an
    ExtractionResult) so that a recorded automation and a YAML-defined
    collection land in the raw data centre through exactly one code path: the
    engine, the validator, the incident opener, and the archiver do not need
    to know which of the two produced the file.
    """

    system: str
    report: str
    destination_dir: Path
    plan: dict[str, Any]
    period: str = ""
    # Authentication filters, identical in meaning to ExtractionRequest.filters:
    # logged_in_selector / login_selector / credential_ref and the unattended
    # login selectors. Replay reuses the same saved session as extraction.
    filters: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 300.0
    run_id: str = ""
    session_state_path: Path | None = None
    evidence_dir: Path | None = None


@dataclass
class ExtractionResult:
    ok: bool
    layer_used: ExtractionLayer
    # One task can produce several files — a summary and its detail, a report per
    # branch. `file_paths` is the real answer; `file_path` stays as the first of
    # them so every existing caller keeps working.
    file_paths: list[Path] = field(default_factory=list)
    file_path: Path | None = None
    original_name: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    # True when the failure was an expired or missing session, not an ordinary error.
    auth_required: bool = False
    # What happened to each recorded step, in order: enough for the run page to
    # show where a task got to, and for a resumed run to know what is already done.
    step_results: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Keep the singular and plural views of the files consistent whichever
        # one the caller filled in.
        if self.file_paths and self.file_path is None:
            self.file_path = self.file_paths[0]
        elif self.file_path is not None and not self.file_paths:
            self.file_paths = [self.file_path]


class BrowserPort(Protocol):
    """Execute one extraction request and return the resulting file plus evidence of what happened."""

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...

    def replay(self, request: ReplayRequest) -> ExtractionResult:
        """Replay a recorded plan step by step and return the file it produced."""
        ...

    def capture_evidence(self, run_id: str) -> dict[str, Any]:
        """Screenshot/trace/network evidence for one failed run, used to build the incident pack."""
        ...
