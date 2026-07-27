"""Worker Registry and the five first-release worker definitions (task 6.1).

A Worker expresses "who acts in which role" and carries the capability allowlist;
a Capability expresses "what may be called". The two are separate so capability
permissions can be reused, routed and governed independently (design Decision 6).
"""

from __future__ import annotations

from agents_orchestration.domain.enums import CapabilityKind, WorkerRole
from agents_orchestration.domain.worker import WorkerDefinition


def _definition(
    role: WorkerRole, allowed: tuple[CapabilityKind, ...], description: str
) -> WorkerDefinition:
    return WorkerDefinition(
        worker_id=f"worker::{role.value}",
        role=role,
        description=description,
        allowed_capabilities=allowed,
    )


def default_worker_definitions() -> tuple[WorkerDefinition, ...]:
    return (
        _definition(
            WorkerRole.RESEARCH_PLANNER, (CapabilityKind.MODEL,), "normalizes goal + plans"
        ),
        _definition(
            WorkerRole.EVIDENCE_RESEARCHER,
            (CapabilityKind.MEMORY_RECALL, CapabilityKind.RAG_SEARCH, CapabilityKind.WEB_RESEARCH),
            "gathers evidence from memory/RAG/web",
        ),
        _definition(
            WorkerRole.ANALYST, (CapabilityKind.MODEL,), "analyzes evidence into conclusions"
        ),
        _definition(WorkerRole.REPORT_WRITER, (CapabilityKind.MODEL,), "writes the report"),
        _definition(WorkerRole.REPORT_REVIEWER, (CapabilityKind.MODEL,), "reviews the report"),
    )


class WorkerRegistry:
    def __init__(self) -> None:
        self._by_role: dict[WorkerRole, WorkerDefinition] = {}

    def register(self, definition: WorkerDefinition) -> None:
        self._by_role[definition.role] = definition

    def get(self, role: WorkerRole) -> WorkerDefinition | None:
        return self._by_role.get(role)

    def all(self) -> list[WorkerDefinition]:
        return list(self._by_role.values())

    @classmethod
    def default(cls) -> WorkerRegistry:
        registry = cls()
        for definition in default_worker_definitions():
            registry.register(definition)
        return registry
