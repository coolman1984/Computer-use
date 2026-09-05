"""حاوية الخدمات: نقطة التركيب الوحيدة لكل مكونات المنصة."""

from __future__ import annotations

import time
from typing import Any, Callable

from .adapters.agents.cli_runner import CliAgentRunner
from .adapters.agents.commands import default_command_builder
from .adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from .adapters.history.archiver import HistoryArchiver
from .adapters.notify.local import CompositeNotifier, LocalLogNotifier, WebhookNotifier
from .adapters.validation.local import LocalFileValidator
from .config import AgentSettings, Settings, ensure_directories, load_settings
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
    RunRepository,
    StepRepository,
    RecordingRepository,
)
from .workflows.profiles import SystemRegistry

# أوضاع الوكيل المدعومة فعليًا في هذه المرحلة من التركيب. Experiment/Execute
# تحتاجان Sandbox واختبار وموافقة بشرية قبل تفعيلهما (راجع docs/MASTER_PLAN.md
# القسم 20)، فلا يُفعَّلان تلقائيًا هنا مهما كان الإعداد.
_SUPPORTED_AGENT_MODES = {"read_only"}


class Services:
    """يجمع الإعدادات وقاعدة البيانات والسجلات والمحرك في كائن واحد."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        db: Database | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        create_directories: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
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

        self.bus = EventBus()
        self.events = EventLog(EventRepository(self.db, self.clock), self.bus, self.clock)

        self.step_registry = StepRegistry()
        self.workflows = WorkflowRegistry()
        self.runner = WorkflowRunner(self, clock=self.clock, sleeper=sleeper)
        self.scheduler = Scheduler(self, clock=self.clock)

        # محوّلات آمنة ومحلية بالكامل: تُركَّب دايمًا، لا تحتاج إعدادًا إضافيًا،
        # ولا تلمس شبكة أو عملية خارجية إلا لو استُدعيت فعليًا من خطوة تشغيل.
        self.validator = LocalFileValidator(files_repo=self.files, now=lambda: self.clock.now().timestamp())
        self.browser = PlaywrightBrowserAdapter(self.settings.browser)
        self.history = HistoryArchiver(self.settings.storage.history_dir)
        # settings.storage.systems_dir، أو فاضي لو المجلد غير موجود. التعريفات
        # الحقيقية تعيش خارج المستودع عبر SMARTOPS_SYSTEMS_DIR (D023).
        self.systems = SystemRegistry.load(self.settings.storage.systems_dir)

        from .recordings.manager import RecordingManager
        self.recording_manager = RecordingManager(self)
        self.recording_manager.recover()
        from .recordings.recovery import RecordingRecovery
        self.recording_recovery = RecordingRecovery(self)

        notifiers: list[Any] = [
            LocalLogNotifier(self.settings.storage.logs_dir / "alerts.jsonl", clock=self.clock)
        ]
        if self.settings.notify.webhook_url:
            notifiers.append(WebhookNotifier(self.settings.notify.webhook_url))
        self.notifier: Any = CompositeNotifier(notifiers)

        # وكيل الذكاء الاصطناعي: مطفأ افتراضيًا (agents.codex/claude.enabled=false).
        # تفعيله قرار تشغيلي وأمني يخص المشغّل، وغير مُختبَر ضد CLI حقيقي هنا.
        self.agent_runner: Any = self._build_agent_runner()

        from .workflows.builtin import register_builtins

        register_builtins(self)

    def _build_agent_runner(self) -> Any:
        """يبني CliAgentRunner لو مفعّل صراحة في الإعداد، وإلا يرجّع None.

        claude له الأولوية لو الاثنان مفعّلان. أي mode غير read_only يرفض
        بخطأ إعداد واضح بدل تفعيل صلاحية أوسع بصمت — Experiment وExecute
        يحتاجان Sandbox واختبار وموافقة بشرية قبل بنائهما (D009، D010).
        """
        agent_name, agent_settings = self._chosen_agent()
        if agent_settings is None:
            return None
        if agent_settings.mode not in _SUPPORTED_AGENT_MODES:
            raise ConfigurationError(
                f"وضع الوكيل غير مدعوم بعد في هذه المرحلة: {agent_settings.mode}",
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

    def close(self) -> None:
        self.db.close()


def build_services(**kwargs: Any) -> Services:
    return Services(**kwargs)
