"""Unit tests for MultiSourceResearchHandler (task 3.4)."""

from __future__ import annotations

from datetime import datetime

import pytest

from agents_orchestration.domain.capability import CapabilityResult
from agents_orchestration.domain.enums import (
    CapabilityKind,
    FailureCode,
    RunState,
    TaskState,
    WorkerRole,
)
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.orchestration.multi_source_handler import MultiSourceResearchHandler
from tests.support.multi_source_doubles import build_fake_multi_source_registry, fake_web_adapter

_NOW = datetime(2026, 1, 1)


def _run() -> Run:
    return Run(
        run_id="run-1",
        raw_goal="AI agent trends",
        state=RunState.RESEARCHING,
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _task(*caps: CapabilityKind, description: str = "evolution trends") -> Task:
    return Task(
        task_id="research-1",
        run_id="run-1",
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
        attempt_id="att-1",
        task_id="research-1",
        run_id="run-1",
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        lease_epoch=1,
        plan_version=1,
        state_version_at_dispatch=1,
        started_at=_NOW,
    )


class _IdGen:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-1"


def _router_invoke(registry):
    """Mimic CapabilityRouter: route by capability_id to the registered adapter."""

    async def invoke(request):
        adapter = registry.adapter(request.capability_id)
        if adapter is None:
            return CapabilityResult.failed(operation_id="op", failure_code=FailureCode.UNAVAILABLE)
        return await adapter.invoke(request)

    return invoke


@pytest.mark.asyncio
async def test_multi_source_joins_all_three_sources():
    registry = build_fake_multi_source_registry()
    handler = MultiSourceResearchHandler(registry, _IdGen())

    result = await handler.handle(
        _task(CapabilityKind.RAG_SEARCH, CapabilityKind.MEMORY_RECALL, CapabilityKind.WEB_RESEARCH),
        _attempt(),
        _run(),
        _router_invoke(registry),
    )

    kinds = {ev.source.source_kind.value for ev in result.evidence}
    assert kinds == {"rag", "memory", "web"}
    assert result.summary.startswith("multi-source:")


@pytest.mark.asyncio
async def test_optional_web_failure_degrades_task_still_succeeds():
    registry = build_fake_multi_source_registry()
    failing_web = fake_web_adapter(fail=True)
    registry.register(failing_web.descriptor, failing_web)  # override the healthy web lane
    handler = MultiSourceResearchHandler(registry, _IdGen())

    result = await handler.handle(
        _task(CapabilityKind.RAG_SEARCH, CapabilityKind.MEMORY_RECALL, CapabilityKind.WEB_RESEARCH),
        _attempt(),
        _run(),
        _router_invoke(registry),
    )

    kinds = {ev.source.source_kind.value for ev in result.evidence}
    assert "web" not in kinds  # OPTIONAL web failed → degraded away
    assert {"rag", "memory"} <= kinds  # required lanes still delivered


@pytest.mark.asyncio
async def test_no_registered_capability_degrades_honestly():
    registry = build_fake_multi_source_registry()
    handler = MultiSourceResearchHandler(registry, _IdGen())

    # MODEL is not registered in the fake registry → no usable lane
    result = await handler.handle(
        _task(CapabilityKind.MODEL),
        _attempt(),
        _run(),
        _router_invoke(registry),
    )

    assert result.summary == "no-capability"
    assert result.evidence == ()


@pytest.mark.asyncio
async def test_query_uses_task_description():
    registry = build_fake_multi_source_registry()
    handler = MultiSourceResearchHandler(registry, _IdGen())

    result = await handler.handle(
        _task(CapabilityKind.RAG_SEARCH, description="glm-5.2 capabilities"),
        _attempt(),
        _run(),
        _router_invoke(registry),
    )

    assert result.evidence
    assert "glm-5.2 capabilities" in result.evidence[0].content_text
