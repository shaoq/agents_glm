"""Integration coverage for one-step-per-tick adaptive research."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agents_orchestration.capabilities.registry import CapabilityRegistry
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.enums import (
    CapabilityKind,
    RunState,
    TaskState,
    WorkerRole,
)
from agents_orchestration.domain.evidence import Usage
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.plan import (
    ExplorationBoundary,
    Plan,
    PlanGraph,
    ResearchExecutionMode,
    SeedExplorationBoundary,
    TaskSpec,
)
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.domain.research_loop import (
    AddDirectionAction,
    QueryAction,
    ResearchAgentDecision,
    ResearchLoopStatus,
    StopRequestAction,
)
from agents_orchestration.orchestration.research_agent_loop import (
    ActionValidator,
    ResearchAgentLoopExecutor,
    ResearchDirectionPolicy,
)
from agents_orchestration.runtime.lease import LeaseManager
from agents_orchestration.runtime.tick import RuntimeTick, TaskExecutionOutcome
from agents_orchestration.workers.registry import WorkerRegistry
from tests.support.multi_source_doubles import fake_rag_adapter

NOW = datetime(2026, 7, 31, tzinfo=UTC)


class _NeverFixedExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task, attempt, run) -> TaskExecutionOutcome:
        self.calls += 1
        return TaskExecutionOutcome(succeeded=True)


class _ScriptedAgent:
    def __init__(self, actions: list[Callable]) -> None:
        self.actions = list(actions)
        self.views = []
        self.request_ids: list[str] = []

    async def decide(self, view, *, decision_request_id: str) -> ResearchAgentDecision:
        self.views.append(view)
        self.request_ids.append(decision_request_id)
        return ResearchAgentDecision(action=self.actions.pop(0)(view))


class _SlowAgent:
    async def decide(self, view, *, decision_request_id: str) -> ResearchAgentDecision:
        await asyncio.sleep(0.04)
        return ResearchAgentDecision(action=_query(view))


class _CostlyAgent:
    async def decide(self, view, *, decision_request_id: str) -> ResearchAgentDecision:
        return ResearchAgentDecision(action=_query(view), usage=Usage(tokens=20))


class _LeaseStealingAgent:
    def __init__(self, backend) -> None:
        self.backend = backend

    async def decide(self, view, *, decision_request_id: str) -> ResearchAgentDecision:
        with self.backend.unit_of_work() as uow:
            LeaseManager(
                uow,
                self.backend.clock,
                self.backend.idgen,
                lease_ttl_seconds=0.03,
            ).claim(
                task_id=view.task_id,
                attempt_id="att-stealer",
                run_id=view.run_id,
            )
            uow.commit()
        await asyncio.sleep(0.04)
        return ResearchAgentDecision(action=_query(view))


def _query(view):
    return QueryAction(
        direction_id=view.directions[-1].direction_id,
        capability_kind=CapabilityKind.RAG_SEARCH,
        query="find supporting evidence",
        rationale="satisfy required coverage",
    )


def _add_direction(view):
    return AddDirectionAction(
        parent_direction_id=view.directions[0].direction_id,
        hint="follow the newly observed claim",
        rationale="evidence revealed a new path",
    )


def _stop(view):
    return StopRequestAction(
        reason="boundary is structurally covered",
        supporting_evidence_ids=tuple(item.evidence_id for item in view.evidence),
        unresolved_questions=(),
    )


def _seed_agent_loop(
    backend,
    *,
    task_ids=("seed-1",),
    max_steps=6,
    max_directions=3,
    budget: Budget | None = None,
    loop_max_cost_usd: Decimal | None = None,
) -> None:
    policy = RunPolicy.from_limits(SystemLimits())
    run = Run(
        run_id="r1",
        raw_goal="research adaptive systems",
        state=RunState.RESEARCHING,
        policy=policy,
        budget=budget or Budget(max_tokens=10_000),
        current_plan_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    specs = tuple(
        TaskSpec(
            task_id=task_id,
            worker_role=WorkerRole.EVIDENCE_RESEARCHER,
            description=f"seed {task_id}",
            required_capabilities=(CapabilityKind.RAG_SEARCH,),
        )
        for task_id in task_ids
    )
    boundary = ExplorationBoundary(
        allowed_capabilities=(CapabilityKind.RAG_SEARCH,),
        seeds=tuple(
            SeedExplorationBoundary(
                task_id=task_id,
                required_coverage=(CapabilityKind.RAG_SEARCH,),
                max_steps=max_steps,
                max_directions=max_directions,
                max_tokens=2_000,
                max_cost_usd=loop_max_cost_usd,
            )
            for task_id in task_ids
        ),
    )
    plan = Plan(
        run_id=run.run_id,
        graph=PlanGraph(
            plan_id="p1",
            version=1,
            research_execution_mode=ResearchExecutionMode.AGENT_LOOP,
            exploration_boundary=boundary,
            task_specs=specs,
        ),
        proposed_at=NOW,
    )
    tasks = tuple(
        Task(
            task_id=spec.task_id,
            run_id=run.run_id,
            plan_version=1,
            worker_role=spec.worker_role,
            required_capabilities=spec.required_capabilities,
            description=spec.description,
            created_at=NOW,
            updated_at=NOW,
        )
        for spec in specs
    )
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.plans.save(plan)
        uow.tasks.materialize(tasks)
        uow.commit()


def _tick(backend, agent, fixed_executor=None) -> RuntimeTick:
    registry = CapabilityRegistry()
    adapter = fake_rag_adapter(tokens=20)
    registry.register(adapter.descriptor, adapter)
    loop_executor = ResearchAgentLoopExecutor(
        agent=agent,
        validator=ActionValidator(),
        direction_policy=ResearchDirectionPolicy(registry.allowed_kinds()),
        registry=registry,
        router=CapabilityRouter(registry, backend.idgen),
        worker=WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER),
    )
    return RuntimeTick(
        backend,
        executor=fixed_executor or _NeverFixedExecutor(),
        agent_loop_executor=loop_executor,
        limits=SystemLimits(),
        base_backoff_seconds=0,
    )


def _tick_with_capability_cost(backend, agent, cost_usd: str) -> RuntimeTick:
    registry = CapabilityRegistry()
    adapter = fake_rag_adapter(tokens=0)
    descriptor = adapter.descriptor.model_copy(update={"cost_usd": Decimal(cost_usd)})
    registry.register(descriptor, adapter)
    loop_executor = ResearchAgentLoopExecutor(
        agent=agent,
        validator=ActionValidator(),
        direction_policy=ResearchDirectionPolicy(registry.allowed_kinds()),
        registry=registry,
        router=CapabilityRouter(registry, backend.idgen),
        worker=WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER),
    )
    return RuntimeTick(
        backend,
        executor=_NeverFixedExecutor(),
        agent_loop_executor=loop_executor,
        limits=SystemLimits(),
    )


@pytest.mark.integration
async def test_tick_advances_only_one_query_step_and_requeues_task(backend) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query, _stop])
    fixed = _NeverFixedExecutor()
    tick = _tick(backend, agent, fixed)

    first = await tick.tick("r1")

    assert first.dispatched == 1 and first.accepted == 1
    assert len(agent.views) == 1
    assert fixed.calls == 0
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        steps = uow.research_steps.by_loop(loop.loop_id)
        evidence = uow.evidence.by_run("r1")
    assert task.state is TaskState.PENDING
    assert loop.step_count == 1 and loop.next_step_index == 1
    assert len(steps) == 1 and steps[0].status.value == "accepted"
    assert len(evidence) == 1
    assert loop.coverage == (CapabilityKind.RAG_SEARCH,)


@pytest.mark.integration
async def test_reasoning_reservation_is_durable_but_only_actual_usage_is_charged(
    backend,
) -> None:
    _seed_agent_loop(backend)
    tick = _tick(backend, _ScriptedAgent([_query]))
    tick.reasoning_reservation_tokens = 7

    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
    assert step.reasoning_reservation.tokens == 7
    assert step.reasoning_usage.tokens == 0
    assert step.capability_usage.tokens == 20
    assert loop.usage.tokens == 20
    assert run.budget.tokens_used == 20


@pytest.mark.integration
async def test_reasoning_reservation_denies_agent_call_when_run_budget_is_too_small(
    backend,
) -> None:
    _seed_agent_loop(backend, budget=Budget(max_tokens=6))
    agent = _ScriptedAgent([_query])
    tick = _tick(backend, agent)
    tick.reasoning_reservation_tokens = 7

    report = await tick.tick("r1")

    assert report.terminal
    assert agent.views == []
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        assert uow.research_loops.by_run("r1", 1) == []
    assert run.termination.value == "budget_exceeded"


@pytest.mark.integration
async def test_query_persists_capability_cost_reservation_and_charges_only_actual_usage(
    backend,
) -> None:
    _seed_agent_loop(
        backend,
        budget=Budget(max_tokens=10_000, max_cost_usd=Decimal("1")),
    )
    tick = _tick_with_capability_cost(
        backend,
        _ScriptedAgent([_query]),
        "0.25",
    )

    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
    assert step.capability_reservation.cost_usd == Decimal("0.25")
    assert step.capability_usage.cost_usd == Decimal("0")
    assert run.budget.cost_usd_used == Decimal("0")


@pytest.mark.integration
async def test_capability_cost_reservation_denies_query_when_global_budget_is_too_small(
    backend,
) -> None:
    _seed_agent_loop(
        backend,
        budget=Budget(max_tokens=10_000, max_cost_usd=Decimal("0.10")),
    )
    tick = _tick_with_capability_cost(
        backend,
        _ScriptedAgent([_query]),
        "0.25",
    )

    report = await tick.tick("r1")

    assert report.terminal
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        evidence = uow.evidence.by_run("r1")
    assert step.capability_reservation.cost_usd == Decimal("0.25")
    assert evidence == []
    assert run.termination.value == "budget_exceeded"


@pytest.mark.integration
async def test_capability_cost_reservation_exhausts_only_the_seed_loop_ceiling(
    backend,
) -> None:
    _seed_agent_loop(
        backend,
        budget=Budget(max_tokens=10_000, max_cost_usd=Decimal("1")),
        loop_max_cost_usd=Decimal("0.10"),
    )
    tick = _tick_with_capability_cost(
        backend,
        _ScriptedAgent([_query]),
        "0.25",
    )

    report = await tick.tick("r1")

    assert not report.terminal
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        evidence = uow.evidence.by_run("r1")
    assert run.state is RunState.RESEARCHING
    assert task.state is TaskState.SUCCEEDED
    assert loop.status is ResearchLoopStatus.EXHAUSTED
    assert loop.degradation_reason == "loop_budget_exhausted"
    assert evidence == []


@pytest.mark.integration
async def test_query_then_add_direction_then_query_then_stop_same_plan(backend) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query, _add_direction, _query, _stop])
    tick = _tick(backend, agent)

    for _ in range(4):
        await tick.tick("r1")

    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        directions = uow.research_directions.by_loop(loop.loop_id)
        steps = uow.research_steps.by_loop(loop.loop_id)
    assert task.state is TaskState.SUCCEEDED
    assert loop.status is ResearchLoopStatus.COMPLETED
    assert len(steps) == 4
    assert len(directions) == 2
    assert loop.direction_count == 1
    assert run.current_plan_version == 1
    assert run.replan_count == 0
    assert run.state is RunState.RESEARCHING


@pytest.mark.integration
async def test_duplicate_direction_records_dedup_without_consuming_direction_budget(
    backend,
) -> None:
    _seed_agent_loop(backend, max_directions=1)
    agent = _ScriptedAgent([_query, _add_direction, _add_direction, _stop])
    tick = _tick(backend, agent)

    for _ in range(4):
        await tick.tick("r1")

    with backend.unit_of_work() as uow:
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        directions = uow.research_directions.by_loop(loop.loop_id)
        events = tuple(uow.events.stream("r1"))
    assert loop.status is ResearchLoopStatus.COMPLETED
    assert loop.direction_count == 1
    assert len(directions) == 2
    assert any(event.effect.value == "research_direction_deduped" for event in events)


@pytest.mark.integration
async def test_early_stop_is_rejected_and_task_remains_schedulable(backend) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_stop])
    tick = _tick(backend, agent)

    report = await tick.tick("r1")

    assert report.accepted == 1
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        events = list(uow.events.stream("r1"))
    assert task.state is TaskState.PENDING
    assert loop.status is ResearchLoopStatus.ACTIVE
    assert any(event.effect.value == "research_stop_rejected" for event in events)


@pytest.mark.integration
async def test_max_steps_exhausts_loop_with_degradation_instead_of_fake_stop(backend) -> None:
    _seed_agent_loop(backend, max_steps=1)
    tick = _tick(backend, _ScriptedAgent([_query]))

    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
    assert task.state is TaskState.SUCCEEDED
    assert loop.status is ResearchLoopStatus.EXHAUSTED
    assert loop.degradation_reason == "max_steps_exhausted"


@pytest.mark.integration
async def test_legacy_fixed_fanout_plan_still_uses_original_executor(backend, fake_clock) -> None:
    policy = RunPolicy.from_limits(SystemLimits())
    run = Run(
        run_id="legacy",
        raw_goal="g",
        state=RunState.RESEARCHING,
        policy=policy,
        current_plan_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    task = Task(
        task_id="legacy-task",
        run_id=run.run_id,
        plan_version=1,
        worker_role=WorkerRole.EVIDENCE_RESEARCHER,
        created_at=NOW,
        updated_at=NOW,
    )
    # No mode/schema fields: this is the semantic equivalent of pre-upgrade JSON.
    plan = Plan(run_id=run.run_id, graph=PlanGraph(plan_id="legacy"), proposed_at=NOW)
    with backend.unit_of_work() as uow:
        uow.runs.save(run, expected_version=1)
        uow.tasks.save(task)
        uow.plans.save(plan)
        uow.commit()
    fixed = _NeverFixedExecutor()
    tick = _tick(backend, _ScriptedAgent([]), fixed)

    await tick.tick(run.run_id)

    assert fixed.calls == 1
    with backend.unit_of_work() as uow:
        assert uow.tasks.get(task.task_id).state is TaskState.SUCCEEDED
        assert uow.research_loops.by_run(run.run_id, 1) == []


@pytest.mark.integration
async def test_multiple_seed_tasks_each_advance_one_step_in_same_tick(backend) -> None:
    _seed_agent_loop(backend, task_ids=("seed-1", "seed-2"))
    agent = _ScriptedAgent([_query, _query])
    report = await _tick(backend, agent).tick("r1")

    assert report.dispatched == 2
    assert len(agent.views) == 2
    with backend.unit_of_work() as uow:
        loops = uow.research_loops.by_run("r1", 1)
        steps = [uow.research_steps.by_loop(loop.loop_id) for loop in loops]
    assert len(loops) == 2
    assert all(len(per_loop) == 1 for per_loop in steps)


@pytest.mark.integration
async def test_late_step_first_failure_uses_step_retry_not_task_attempt_count(
    backend,
) -> None:
    _seed_agent_loop(backend, max_steps=8)

    def fail(_view):
        raise TimeoutError("temporary")

    agent = _ScriptedAgent([_query, _query, _query, fail, _query])
    tick = _tick(backend, agent)
    for _ in range(3):
        await tick.tick("r1")

    failed = await tick.tick("r1")
    assert failed.retried == 1
    with backend.unit_of_work() as uow:
        task = uow.tasks.get("seed-1")
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 3)
    assert task.attempt_count == 4
    assert task.state is TaskState.AWAITING_RETRY
    assert step.retry_count == 1
    assert loop.status is ResearchLoopStatus.ACTIVE

    recovered = await tick.tick("r1")
    assert recovered.accepted == 1


@pytest.mark.integration
async def test_restart_recovers_deciding_step_with_same_logical_identity(
    backend,
    fake_clock,
    monkeypatch,
) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query])
    tick = _tick(backend, agent)
    original_execute = tick._execute_agent_dispatch

    async def crash_after_dispatch(_dispatch, _run):
        raise RuntimeError("process exited before Agent call")

    monkeypatch.setattr(tick, "_execute_agent_dispatch", crash_after_dispatch)
    with pytest.raises(RuntimeError, match="before Agent"):
        await tick.tick("r1")

    with backend.unit_of_work() as uow:
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        deciding = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
    assert deciding.status.value == "deciding"
    assert agent.views == []

    fake_clock.advance(60)
    monkeypatch.setattr(tick, "_execute_agent_dispatch", original_execute)
    recovered = await tick.tick("r1")

    with backend.unit_of_work() as uow:
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        steps = uow.research_steps.by_loop(loop.loop_id)
    assert recovered.accepted == 1
    assert len(agent.views) == 1
    assert len(steps) == 1
    assert step.step_id == deciding.step_id
    assert step.decision_request_id == deciding.decision_request_id
    assert step.lease_epoch == 2


@pytest.mark.integration
async def test_restart_recovers_prepared_step_without_repeating_agent_usage(
    backend,
    fake_clock,
    monkeypatch,
) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query])
    tick = _tick(backend, agent)
    tick.reasoning_reservation_tokens = 7
    original_persist = tick._persist_prepared_action

    def crash_after_prepare(dispatch, decision):
        original_persist(dispatch, decision)
        raise RuntimeError("process exited after PREPARED")

    monkeypatch.setattr(tick, "_persist_prepared_action", crash_after_prepare)
    with pytest.raises(RuntimeError, match="after PREPARED"):
        await tick.tick("r1")

    with backend.unit_of_work() as uow:
        prepared = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        run_after_crash = uow.runs.get("r1")
    assert prepared.status.value == "prepared"
    assert len(agent.views) == 1

    fake_clock.advance(60)
    monkeypatch.setattr(tick, "_persist_prepared_action", original_persist)
    recovered = await tick.tick("r1")

    with backend.unit_of_work() as uow:
        accepted = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        run = uow.runs.get("r1")
    assert recovered.accepted == 1
    assert len(agent.views) == 1
    assert accepted.step_id == prepared.step_id
    assert accepted.status.value == "accepted"
    assert run.budget.tokens_used - run_after_crash.budget.tokens_used == 20


@pytest.mark.integration
async def test_capability_success_before_accept_reconciles_with_stable_operation(
    backend,
    fake_clock,
    monkeypatch,
) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query])
    tick = _tick(backend, agent)
    original_accept = tick._accept
    observed_operation_ids: list[str] = []

    async def crash_before_accept(_run_id, outcomes):
        result = outcomes[0][1].capability_result
        observed_operation_ids.append(result.operation_id)
        raise RuntimeError("process exited before accept")

    monkeypatch.setattr(tick, "_accept", crash_before_accept)
    with pytest.raises(RuntimeError, match="before accept"):
        await tick.tick("r1")

    with backend.unit_of_work() as uow:
        step_before = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        assert uow.evidence.by_run("r1") == []
    assert step_before.status.value == "prepared"

    fake_clock.advance(60)
    monkeypatch.setattr(tick, "_accept", original_accept)
    recovered = await tick.tick("r1")

    with backend.unit_of_work() as uow:
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        evidence = uow.evidence.by_run("r1")
        attempts = uow.attempts.by_run("r1")
        operations = [
            operation
            for attempt in attempts
            for operation in uow.operations.by_attempt(attempt.attempt_id)
        ]
        run = uow.runs.get("r1")
    assert recovered.accepted == 1
    assert len(agent.views) == 1
    assert len(evidence) == 1
    assert len(operations) == 1
    assert operations[0].outcome_certainty.value == "confirmed"
    assert step.result_operation_id == observed_operation_ids[0]
    assert run.budget.tokens_used == 20


@pytest.mark.integration
async def test_pause_resume_preserves_loop_progress_and_creates_new_claim(
    backend,
) -> None:
    _seed_agent_loop(backend)
    agent = _ScriptedAgent([_query, _stop])
    tick = _tick(backend, agent)
    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
        paused = run.transition(RunState.PAUSED, backend.clock.now()).model_copy(
            update={"paused_from_state": RunState.RESEARCHING}
        )
        uow.runs.save(paused, expected_version=run.state_version)
        uow.commit()

    blocked = await tick.tick("r1")
    assert blocked.blocked and blocked.dispatched == 0

    with backend.unit_of_work() as uow:
        current = uow.runs.get("r1")
        resumed = current.transition(RunState.RESEARCHING, backend.clock.now()).model_copy(
            update={"paused_from_state": None}
        )
        uow.runs.save(resumed, expected_version=current.state_version)
        uow.commit()

    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        loop = uow.research_loops.for_task("r1", 1, "seed-1")
        attempts = uow.attempts.by_run("r1")
        task = uow.tasks.get("seed-1")
    assert loop.status is ResearchLoopStatus.COMPLETED
    assert loop.step_count == 2
    assert loop.next_step_index == 2
    assert loop.coverage == (CapabilityKind.RAG_SEARCH,)
    assert loop.usage.tokens == 20
    assert len(attempts) == 2
    assert {attempt.lease_epoch for attempt in attempts} == {1, 2}
    assert task.state is TaskState.SUCCEEDED


@pytest.mark.integration
async def test_long_decision_renews_same_lease_epoch(backend) -> None:
    _seed_agent_loop(backend)
    registry = CapabilityRegistry()
    adapter = fake_rag_adapter()
    registry.register(adapter.descriptor, adapter)
    loop_executor = ResearchAgentLoopExecutor(
        agent=_SlowAgent(),
        validator=ActionValidator(),
        direction_policy=ResearchDirectionPolicy(registry.allowed_kinds()),
        registry=registry,
        router=CapabilityRouter(registry, backend.idgen),
        worker=WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER),
    )
    tick = RuntimeTick(
        backend,
        executor=_NeverFixedExecutor(),
        agent_loop_executor=loop_executor,
        limits=SystemLimits(),
        lease_ttl_seconds=0.03,
    )

    await tick.tick("r1")

    with backend.unit_of_work() as uow:
        lease = uow.leases.get("seed-1")
        attempts = uow.attempts.by_run("r1")
    assert lease.epoch == 1
    assert lease.attempt_id == attempts[0].attempt_id
    assert lease.state.value == "released"
    assert lease.claimed_at > attempts[0].started_at


@pytest.mark.integration
async def test_replaced_lease_fences_long_running_agent_result(backend) -> None:
    _seed_agent_loop(backend)
    registry = CapabilityRegistry()
    adapter = fake_rag_adapter()
    registry.register(adapter.descriptor, adapter)
    loop_executor = ResearchAgentLoopExecutor(
        agent=_LeaseStealingAgent(backend),
        validator=ActionValidator(),
        direction_policy=ResearchDirectionPolicy(registry.allowed_kinds()),
        registry=registry,
        router=CapabilityRouter(registry, backend.idgen),
        worker=WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER),
    )
    tick = RuntimeTick(
        backend,
        executor=_NeverFixedExecutor(),
        agent_loop_executor=loop_executor,
        limits=SystemLimits(),
        lease_ttl_seconds=0.03,
    )

    report = await tick.tick("r1")

    assert report.rejected_late == 1
    with backend.unit_of_work() as uow:
        step = uow.research_steps.by_logical_key("r1", 1, "seed-1", 0)
        evidence = uow.evidence.by_run("r1")
        run = uow.runs.get("r1")
    assert step.status.value == "deciding"
    assert evidence == []
    assert run.budget.tokens_used == 0


@pytest.mark.integration
async def test_global_budget_overrun_terminates_instead_of_normal_stop(backend) -> None:
    _seed_agent_loop(backend, budget=Budget(max_tokens=10))
    tick = _tick(backend, _CostlyAgent())

    report = await tick.tick("r1")

    assert report.terminal
    with backend.unit_of_work() as uow:
        run = uow.runs.get("r1")
    assert run.state is RunState.FAILED
    assert run.termination.value == "budget_exceeded"
