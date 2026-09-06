"""The services container: the single wiring point for every platform component."""

from __future__ import annotations

import time
from typing import Any, Callable

from .adapters.agents.cli_runner import CliAgentRunner
from .adapters.agents.commands import default_command_builder
from .adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from .adapters.history.archiver import HistoryArchiver
from .adapters.notify.local import CompositeNotifier, LocalLogNotifier, WebhookNotifier
from .adapters.validation.local import LocalFileValidator
from .checks import ConnectionCheckStore
from .config import AgentSettings, Settings, ensure_directories, load_settings
from .credentials import CredentialStore, default_credential_store
from .core.clock import Clock, SystemClock
from .core.errors import ConfigurationError
from .engine.registry import StepRegistry, WorkflowRegistry
from .engine.runner import WorkflowRunner
from .events.bus import EventBus
from .events.log import EventLog
from .scheduler import Scheduler
from .storage.db import Database
from .storage.repositories import (
    AgentRunRepository,
    EventRepository,
    FileRepository,
    IncidentRepository,
    ProcessRepository,
    RunRepository,
    StepRepository,
    RecordingRepository,
)
from .workflows.profiles import SystemRegistry

# Agent modes actually supported at this stage of the build. Experiment/Execute
# need a sandbox, testing, and human approval before they can be enabled (see
# docs/MASTER_PLAN.md section 20), so they are never auto-enabled here regardless of settings.
_SUPPORTED_AGENT_MODES = {"read_only"}


class Services:
    """Gathers settings, the database, the repositories, and the engine into one object."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        db: Database | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        create_directories: bool = True,
        credential_store: CredentialStore | None = None,
        recover_on_start: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        if (
            self.settings.browser.record_headless
            and self.settings.safety.allow_development_features is not True
        ):
            raise ConfigurationError(
                "Headless recording is a development-only feature. Enable "
                "safety.allow_development_features explicitly in an isolated test setup."
            )
        self.clock = clock or SystemClock()
        if create_directories:
            ensure_directories(self.settings)
        self.db = db or Database(self.settings.storage.sqlite_path)
        self.db.migrate()

        self.runs = RunRepository(self.db, self.clock)
        self.steps = StepRepository(self.db, self.clock)
        self.files = FileRepository(self.db, self.clock)
        self.incidents = IncidentRepository(self.db, self.clock)
        self.agent_runs = AgentRunRepository(self.db, self.clock)
        self.recordings = RecordingRepository(self.db, self.clock)
        self.processes = ProcessRepository(self.db, self.clock)

        self.bus = EventBus()
        self.events = EventLog(EventRepository(self.db, self.clock), self.bus, self.clock)

        self.step_registry = StepRegistry()
        self.workflows = WorkflowRegistry()
        self.runner = WorkflowRunner(self, clock=self.clock, sleeper=sleeper)
        self.scheduler = Scheduler(self, clock=self.clock)

        # Fully local, safe adapters: always wired up, need no extra
        # configuration, and never touch the network or an external process
        # unless actually invoked from a run step.
        self.validator = LocalFileValidator(files_repo=self.files, now=lambda: self.clock.now().timestamp())
        self.credentials = credential_store or default_credential_store()
        self.browser = PlaywrightBrowserAdapter(self.settings.browser, credential_store=self.credentials)
        self.history = HistoryArchiver(self.settings.storage.history_dir)
        # settings.storage.systems_dir, or empty if the folder does not
        # exist. Real definitions live outside the repo via SMARTOPS_SYSTEMS_DIR (D023).
        self.systems = SystemRegistry.load(self.settings.storage.systems_dir)

        # Which systems have passed a connection test. Durable on purpose: a
        # restart must not silently undo a completed stage of the journey.
        self.connection_checks = ConnectionCheckStore(
            self.settings.storage.logs_dir / "connection-checks.json"
        )

        from .processes.manager import ProcessManager
        self.process_manager = ProcessManager(self)
        from .login import LoginManager
        self.login_manager = LoginManager(self)

        from .recordings.manager import RecordingManager
        self.recording_manager = RecordingManager(self)
        from .recordings.recovery import RecordingRecovery
        self.recording_recovery = RecordingRecovery(self)

        notifiers: list[Any] = [
            LocalLogNotifier(self.settings.storage.logs_dir / "alerts.jsonl", clock=self.clock)
        ]
        if self.settings.notify.webhook_url:
            notifiers.append(WebhookNotifier(self.settings.notify.webhook_url))
        self.notifier: Any = CompositeNotifier(notifiers)

        # AI agent: off by default (agents.codex/claude.enabled=false).
        # Enabling it is an operational and security decision for the
        # operator, and is not tested against a real CLI here.
        self.agent_runner: Any = self._build_agent_runner()

        from .workflows.builtin import register_builtins

        register_builtins(self)

        # Last, because it needs every repository and manager above it. Starting
        # the platform is the only trigger a non-technical user will ever pull,
        # so it is the one that has to clear whatever a crash left behind —
        # interrupted runs, half-finished tests, dead recordings.
        from .recovery import RecoveryService

        self.recovery = RecoveryService(self)
        if recover_on_start:
            self.recovery.recover_all()

    def _build_agent_runner(self) -> Any:
        """Build a CliAgentRunner if explicitly enabled in settings, otherwise return None.

        Claude takes priority if both are enabled. Any mode other than
        read_only is rejected with a clear configuration error instead of
        silently enabling broader permission — Experiment and Execute need a
        sandbox, testing, and human approval before they can be built (D009, D010).
        """
        agent_name, agent_settings = self._chosen_agent()
        if agent_settings is None:
            return None
        if agent_settings.mode not in _SUPPORTED_AGENT_MODES:
            raise ConfigurationError(
                f"Agent mode is not yet supported at this stage: {agent_settings.mode}",
                details={"agent": agent_name, "supported_modes": sorted(_SUPPORTED_AGENT_MODES)},
            )
        executable = agent_settings.executable or agent_name
        return CliAgentRunner(default_command_builder(executable))

    def _chosen_agent(self) -> tuple[str, AgentSettings | None]:
        if self.settings.agents.claude.enabled:
            return "claude", self.settings.agents.claude
        if self.settings.agents.codex.enabled:
            return "codex", self.settings.agents.codex
        return "", None

    def reload_systems(self) -> None:
        """Re-read system definitions from disk after a change made in the app.

        Without this, adding a system would still mean restarting the server —
        the reason step one of the journey used to live outside the product.
        """
        self.systems.reload()

    def close(self) -> None:
        self.db.close()


def build_services(**kwargs: Any) -> Services:
    return Services(**kwargs)
