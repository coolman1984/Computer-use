"""Two registries: step executors, and workflow definitions."""

from __future__ import annotations

from typing import Callable

from ..core.errors import ConfigurationError
from ..domain.models import WorkflowDefinition
from .contracts import Step


class StepRegistry:
    def __init__(self) -> None:
        self._steps: dict[str, Step] = {}

    def register(self, key: str) -> Callable[[Step], Step]:
        def decorator(step: Step) -> Step:
            self._steps[key] = step
            return step

        return decorator

    def add(self, key: str, step: Step) -> None:
        self._steps[key] = step

    def get(self, key: str) -> Step:
        if key not in self._steps:
            raise ConfigurationError(f"Step executor is not registered: {key}", details={"uses": key})
        return self._steps[key]

    def keys(self) -> list[str]:
        return sorted(self._steps)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[definition.key] = definition
        return definition

    def get(self, key: str) -> WorkflowDefinition:
        if key not in self._workflows:
            raise ConfigurationError(f"Workflow is not defined: {key}", details={"workflow": key})
        return self._workflows[key]

    def list(self) -> list[WorkflowDefinition]:
        return [self._workflows[key] for key in sorted(self._workflows)]
