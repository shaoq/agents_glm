"""Integration tests for capability registry, router and worker executor (Section 6)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents_orchestration.capabilities.registry import CapabilityRegistry, WriteCapabilityRejected
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.capability import (
    CapabilityDescriptor,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
)
from agents_orchestration.domain.enums import CapabilityKind, FailureCode, WorkerRole
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.workers.executor import WorkerExecutor
from agents_orchestration.workers.registry import WorkerRegistry

NOW = datetime(2026, 7, 27, tzinfo=UTC)


class _FakeAdapter:
    def __init__(self, descriptor: CapabilityDescriptor, result: CapabilityResult) -> None:
        self.descriptor = descriptor
        self._result = result

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:
        return self._result


def _descriptor(cap_id: str, kind: CapabilityKind) -> CapabilityDescriptor:
    return CapabilityDescriptor(capability_id=cap_id, kind=kind.value)


def _request(cap_id: str, **inputs) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="req",
        capability_id=cap_id,
        worker_id="w",
        run_id="r1",
        task_id="t1",
        attempt_id="a1",
        inputs=dict(inputs),
    )


def _ok_result() -> CapabilityResult:
    return CapabilityResult.ok(
        operation_id="op",
        evidence=(
            Evidence(
                evidence_id="e1",
                source=SourceIdentity(source_id="s1", source_kind=SourceKind.RAG, uri="uri:s1"),
                content_text="fact",
            ),
        ),
    )


# --- 6.4 / 12.5 read-only enforcement ---------------------------------------


@pytest.mark.integration
def test_registry_rejects_write_capability() -> None:
    registry = CapabilityRegistry()
    desc = _descriptor("publish", CapabilityKind.MODEL).model_copy(
        update={"permission": CapabilityPermission.WRITE}
    )
    with pytest.raises(WriteCapabilityRejected):
        registry.register(desc, _FakeAdapter(desc, _ok_result()))


@pytest.mark.integration
def test_registry_allowed_kinds_track_readonly_descriptors() -> None:
    registry = CapabilityRegistry()
    rag = _descriptor("rag", CapabilityKind.RAG_SEARCH)
    memory = _descriptor("memory", CapabilityKind.MEMORY_RECALL)
    registry.register(rag, _FakeAdapter(rag, _ok_result()))
    registry.register(memory, _FakeAdapter(memory, _ok_result()))
    assert registry.allowed_kinds() == frozenset(
        {CapabilityKind.RAG_SEARCH, CapabilityKind.MEMORY_RECALL}
    )


# --- 6.5 Router policy enforcement ------------------------------------------


@pytest.mark.integration
async def test_router_denies_capability_outside_worker_allowlist(backend) -> None:
    registry = CapabilityRegistry()
    model = _descriptor("model", CapabilityKind.MODEL)
    registry.register(model, _FakeAdapter(model, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    assert researcher is not None
    result = await router.invoke(
        _request("model"),
        worker=researcher,
        run_policy=RunPolicy.from_limits(SystemLimits()),
    )
    assert result.status.value == "failed" and result.failure_code is FailureCode.FORBIDDEN


@pytest.mark.integration
async def test_router_allows_permitted_capability(backend) -> None:
    registry = CapabilityRegistry()
    rag = _descriptor("rag", CapabilityKind.RAG_SEARCH)
    registry.register(rag, _FakeAdapter(rag, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    result = await router.invoke(
        _request("rag"),
        worker=researcher,
        run_policy=RunPolicy.from_limits(SystemLimits()),
    )
    assert result.succeeded


@pytest.mark.integration
async def test_router_denies_web_when_policy_disabled(backend) -> None:
    registry = CapabilityRegistry()
    web = _descriptor("web", CapabilityKind.WEB_RESEARCH)
    registry.register(web, _FakeAdapter(web, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    result = await router.invoke(
        _request("web", url="https://example.com/x"),
        worker=researcher,
        run_policy=RunPolicy.from_limits(SystemLimits()),
    )
    assert result.failure_code is FailureCode.UNAUTHORIZED


@pytest.mark.integration
async def test_router_denies_disallowed_domain(backend) -> None:
    registry = CapabilityRegistry()
    web = _descriptor("web", CapabilityKind.WEB_RESEARCH)
    registry.register(web, _FakeAdapter(web, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    policy = RunPolicy.from_limits(
        SystemLimits(), web_enabled=True, web_allowed_domains=("allowed.com",)
    )
    result = await router.invoke(
        _request("web", url="https://evil.com/x"),
        worker=researcher,
        run_policy=policy,
    )
    assert result.failure_code is FailureCode.FORBIDDEN


# --- 6.8 contract round-trip ------------------------------------------------


@pytest.mark.integration
async def test_fake_adapter_contract_round_trip(backend) -> None:
    registry = CapabilityRegistry()
    rag = _descriptor("rag", CapabilityKind.RAG_SEARCH)
    registry.register(rag, _FakeAdapter(rag, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    result = await router.invoke(
        _request("rag"),
        worker=researcher,
        run_policy=RunPolicy.from_limits(SystemLimits()),
    )
    assert result.succeeded and result.evidence[0].source.source_kind is SourceKind.RAG


# --- 6.2 / 6.3 / 6.9 executor + cannot expand capabilities ------------------


@pytest.mark.integration
async def test_worker_cannot_expand_capabilities(backend, fake_clock) -> None:
    registry = CapabilityRegistry()
    model = _descriptor("model", CapabilityKind.MODEL)
    registry.register(model, _FakeAdapter(model, _ok_result()))
    router = CapabilityRouter(registry, backend.idgen)

    attempted: list[str] = []

    class _GreedyHandler:
        async def handle(self, task, attempt, run, invoke) -> TaskResult:
            res = await invoke(_request("model"))
            attempted.append(res.status.value)
            return TaskResult(
                attempt_id=attempt.attempt_id,
                task_id=task.task_id,
                run_id=run.run_id,
                worker_role=task.worker_role,
                summary="ok",
            )

    task = Task(
        task_id="t1",
        run_id="r1",
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        created_at=NOW,
        updated_at=NOW,
    )
    attempt = _attempt_for(task)
    run = _run_for(fake_clock)
    executor = WorkerExecutor(
        WorkerRegistry.default(),
        router,
        {WorkerRole.EVIDENCE_RESEARCHER: _GreedyHandler()},
        RunPolicy.from_limits(SystemLimits()),
    )
    outcome = await executor.execute(task, attempt, run)
    # The router denied MODEL to the researcher; the handler still returned a
    # TaskResult, but it could not actually expand capability permissions.
    assert attempted == ["failed"]
    assert outcome.succeeded


@pytest.mark.integration
async def test_worker_executor_rejects_non_taskresult_output(backend, fake_clock) -> None:
    registry = CapabilityRegistry()
    router = CapabilityRouter(registry, backend.idgen)

    class _BadHandler:
        async def handle(self, task, attempt, run, invoke):
            return {"not": "a task result"}  # type: ignore[return-value]

    task = Task(
        task_id="t1",
        run_id="r1",
        plan_version=1,
        worker_role=WorkerRole.ANALYST,
        created_at=NOW,
        updated_at=NOW,
    )
    executor = WorkerExecutor(
        WorkerRegistry.default(),
        router,
        {WorkerRole.ANALYST: _BadHandler()},
        RunPolicy.from_limits(SystemLimits()),
    )
    outcome = await executor.execute(task, _attempt_for(task), _run_for(fake_clock))
    assert not outcome.succeeded and outcome.failure_code is FailureCode.INVALID_RESPONSE


def _attempt_for(task: Task) -> Attempt:
    return Attempt(
        attempt_id="a1",
        task_id=task.task_id,
        run_id=task.run_id,
        worker_role=task.worker_role,
        lease_epoch=1,
        plan_version=1,
        state_version_at_dispatch=1,
        started_at=NOW,
    )


def _run_for(clock) -> Run:
    now = clock.now()
    return Run(
        run_id="r1",
        raw_goal="g",
        policy=RunPolicy.from_limits(SystemLimits()),
        created_at=now,
        updated_at=now,
    )
