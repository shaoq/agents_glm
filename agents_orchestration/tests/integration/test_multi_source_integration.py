"""Integration tests: multi-source research via real composition wiring.

Covers task 6.1 (end-to-end multi-source through the real ``CapabilityRouter``)
and task 4.5 (the production composition seam injects fake doubles). The phase
handlers are unchanged by this change, so phase-level Join is still covered by
``test_phase_research.py``; here we exercise the worker-handler → router →
adapter wiring that ``build_production_coordinator_from_settings`` assembles.
"""

from __future__ import annotations

import datetime
import pathlib
import re

import pytest

import agents_orchestration
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.enums import CapabilityKind, RunState, TaskState, WorkerRole
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.multi_source_handler import MultiSourceResearchHandler
from agents_orchestration.workers.executor import WorkerExecutor
from agents_orchestration.workers.registry import WorkerRegistry
from tests.support.multi_source_doubles import build_fake_multi_source_registry, fake_web_adapter

_NOW = datetime.datetime(2026, 1, 1)


def _run() -> Run:
    return Run(
        run_id="run-e2e",
        raw_goal="AI agent evolution",
        state=RunState.RESEARCHING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _task(*caps: CapabilityKind, description: str = "evolution trends") -> Task:
    return Task(
        task_id="research-e2e",
        run_id="run-e2e",
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        state=TaskState.PENDING,
        required_capabilities=tuple(caps),
        description=description,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _attempt() -> Attempt:
    return Attempt(
        attempt_id="att-e2e",
        task_id="research-e2e",
        run_id="run-e2e",
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        lease_epoch=1,
        plan_version=1,
        state_version_at_dispatch=1,
        started_at=_NOW,
    )


class _IdGen:
    _n = 0

    def new_id(self, prefix: str) -> str:
        _IdGen._n += 1
        return f"{prefix}-{_IdGen._n}"


def _executor(registry) -> WorkerExecutor:
    """Assemble the real WorkerExecutor + Router + multi-source handler."""
    router = CapabilityRouter(registry, _IdGen())
    handler = MultiSourceResearchHandler(registry, _IdGen())
    workers = WorkerRegistry.default()
    # web_enabled=True so the WEB_RESEARCH lane clears the Router's web policy
    # guard (router.py:47); production leaves it False until sibling wiring lands.
    policy = RunPolicy.from_limits(SystemLimits(), web_enabled=True)
    handlers = {WorkerRole.EVIDENCE_RESEARCHER: handler}
    return WorkerExecutor(workers, router, handlers, policy)


@pytest.mark.integration
async def test_multi_source_research_through_real_router() -> None:
    registry = build_fake_multi_source_registry()
    executor = _executor(registry)

    outcome = await executor.execute(
        _task(CapabilityKind.RAG_SEARCH, CapabilityKind.MEMORY_RECALL, CapabilityKind.WEB_RESEARCH),
        _attempt(),
        _run(),
    )

    assert outcome.succeeded
    kinds = {ev.source.source_kind.value for ev in outcome.task_result.evidence}
    assert kinds == {"rag", "memory", "web"}


@pytest.mark.integration
async def test_optional_web_failure_degrades_through_real_router() -> None:
    registry = build_fake_multi_source_registry()
    failing = fake_web_adapter(fail=True)
    registry.register(failing.descriptor, failing)  # override healthy web lane
    executor = _executor(registry)

    outcome = await executor.execute(
        _task(CapabilityKind.RAG_SEARCH, CapabilityKind.MEMORY_RECALL, CapabilityKind.WEB_RESEARCH),
        _attempt(),
        _run(),
    )

    assert outcome.succeeded  # task still succeeds — web lane degraded, not fatal
    kinds = {ev.source.source_kind.value for ev in outcome.task_result.evidence}
    assert "web" not in kinds
    assert {"rag", "memory"} <= kinds


def test_production_src_contains_no_fake_classes() -> None:
    """Architecture (task 6.4): production code contains no Fake classes — doubles
    live under tests/ per remove-offline-fake-assembly."""

    root = pathlib.Path(agents_orchestration.__file__).parent
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for match in re.finditer(r"^class (Fake\w+)", text, re.MULTILINE):
            offenders.append(f"{py.relative_to(root)}: {match.group(1)}")
    assert not offenders, f"Fake classes found in production src/: {offenders}"


def test_production_composition_dropped_llm_research_handler() -> None:
    """The single-source _LLMResearchHandler is removed (replaced by
    MultiSourceResearchHandler); production wires the multi-source handler."""

    root = pathlib.Path(agents_orchestration.__file__).parent
    composition = (root / "orchestration" / "composition.py").read_text(encoding="utf-8")
    assert "_LLMResearchHandler" not in composition
    assert "MultiSourceResearchHandler" in composition
