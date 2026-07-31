"""Deterministic Plan validation and acceptance (tasks 5.5 / 5.6 / 5.7).

The model proposes a :class:`PlanProposal`; the deterministic PlanValidator
checks DAG, cycle, registry, permission, budget, depth, concurrency and
deliverable coverage; the PlanAcceptor atomically materializes the accepted plan
(validate-before-materialize, so rejection leaves no partial Tasks).
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_orchestration.domain.enums import (
    CapabilityKind,
    EffectType,
    RunState,
    TaskState,
    WorkerRole,
)
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.goal import CompletionContract
from agents_orchestration.domain.plan import Plan, PlanGraph, ResearchExecutionMode
from agents_orchestration.domain.policy import Budget, RunPolicy, SystemLimits
from agents_orchestration.orchestration.proposals import PlanProposal

_FIXED_LIFECYCLE_DELIVERABLES = frozenset({"report.md"})


@dataclass(frozen=True)
class PlanValidation:
    accepted: bool
    diagnostics: tuple[str, ...]
    graph: PlanGraph | None


class PlanValidator:
    """Deterministic validation of a PlanProposal (task 5.5)."""

    def __init__(self, limits: SystemLimits) -> None:
        self.limits = limits

    def validate(
        self,
        proposal: PlanProposal,
        *,
        policy: RunPolicy,
        allowed_capabilities: frozenset[CapabilityKind],
        completion: CompletionContract,
        version: int = 1,
        run_budget: Budget | None = None,
    ) -> PlanValidation:
        graph = proposal.to_graph(version)
        diagnostics: list[str] = []

        if graph.task_count > policy.max_tasks:
            diagnostics.append(f"task_count {graph.task_count} > policy {policy.max_tasks}")
        if graph.task_count > self.limits.max_tasks:
            diagnostics.append(f"task_count {graph.task_count} > system {self.limits.max_tasks}")
        if graph.max_depth > policy.max_plan_depth:
            diagnostics.append(f"depth {graph.max_depth} > policy {policy.max_plan_depth}")
        if graph.max_depth > self.limits.max_plan_depth:
            diagnostics.append(f"depth {graph.max_depth} > system {self.limits.max_plan_depth}")
        if graph.task_count > 0 and graph.has_cycle():
            diagnostics.append("plan contains a cycle")

        ids = set(graph.task_ids)
        for spec in graph.task_specs:
            if spec.worker_role is not WorkerRole.EVIDENCE_RESEARCHER:
                diagnostics.append(
                    f"task {spec.task_id} has non-research role "
                    f"{spec.worker_role.value}; only evidence_researcher Tasks "
                    "are dispatchable"
                )
            for cap in spec.required_capabilities:
                if cap not in allowed_capabilities:
                    diagnostics.append(
                        f"task {spec.task_id} requires unsupported capability {cap.value}"
                    )
            for dep in spec.depends_on:
                if dep not in ids:
                    diagnostics.append(f"task {spec.task_id} depends on unknown task {dep}")
        for dep in graph.dependencies:
            if dep.predecessor not in ids or dep.successor not in ids:
                diagnostics.append(
                    f"dependency references unknown task {dep.predecessor}->{dep.successor}"
                )

        self._validate_research_mode(
            graph,
            diagnostics=diagnostics,
            policy=policy,
            allowed_capabilities=allowed_capabilities,
            run_budget=run_budget,
        )

        produced = {s.deliverable_path for s in graph.task_specs if s.deliverable_path}
        produced |= set(proposal.deliverable_paths)
        for path in completion.deliverable_paths:
            if path in _FIXED_LIFECYCLE_DELIVERABLES:
                continue
            if path not in produced:
                diagnostics.append(f"required deliverable {path} is not produced by the Plan")

        accepted = not diagnostics and graph.task_count > 0
        return PlanValidation(accepted, tuple(diagnostics), graph if accepted else None)

    def _validate_research_mode(
        self,
        graph: PlanGraph,
        *,
        diagnostics: list[str],
        policy: RunPolicy,
        allowed_capabilities: frozenset[CapabilityKind],
        run_budget: Budget | None,
    ) -> None:
        boundary = graph.exploration_boundary
        if graph.research_execution_mode is ResearchExecutionMode.FIXED_FANOUT:
            if boundary is not None:
                diagnostics.append("fixed_fanout Plan must not define an exploration boundary")
            return
        if boundary is None:  # PlanGraph normally rejects this; keep the validator defensive.
            diagnostics.append("agent_loop Plan requires an exploration boundary")
            return

        boundary_allowed = set(boundary.allowed_capabilities)
        if not boundary_allowed.issubset(allowed_capabilities):
            extra = sorted(cap.value for cap in boundary_allowed - allowed_capabilities)
            diagnostics.append(f"boundary contains unsupported capabilities {extra}")

        task_by_id = {spec.task_id: spec for spec in graph.task_specs}
        seed_ids = {seed.task_id for seed in boundary.seeds}
        task_ids = set(task_by_id)
        if seed_ids != task_ids:
            diagnostics.append(
                "seed boundary task ids must exactly match Plan seed Tasks "
                f"(boundary={sorted(seed_ids)}, tasks={sorted(task_ids)})"
            )

        total_tokens = 0
        total_cost = 0
        token_ceiling_complete = True
        cost_ceiling_complete = True
        for seed in boundary.seeds:
            spec = task_by_id.get(seed.task_id)
            if spec is not None and not set(spec.required_capabilities).issubset(
                set(seed.required_coverage)
            ):
                diagnostics.append(
                    f"seed {seed.task_id} required coverage must include Task required capabilities"
                )
            if seed.max_steps > policy.max_research_steps_per_seed:
                diagnostics.append(
                    f"seed {seed.task_id} max_steps {seed.max_steps} > policy "
                    f"{policy.max_research_steps_per_seed}"
                )
            if seed.max_steps > self.limits.max_research_steps_per_seed:
                diagnostics.append(
                    f"seed {seed.task_id} max_steps {seed.max_steps} > system "
                    f"{self.limits.max_research_steps_per_seed}"
                )
            if seed.max_directions > policy.max_research_directions_per_seed:
                diagnostics.append(
                    f"seed {seed.task_id} max_directions {seed.max_directions} > policy "
                    f"{policy.max_research_directions_per_seed}"
                )
            if seed.max_directions > self.limits.max_research_directions_per_seed:
                diagnostics.append(
                    f"seed {seed.task_id} max_directions {seed.max_directions} > system "
                    f"{self.limits.max_research_directions_per_seed}"
                )
            if seed.max_tokens is None:
                token_ceiling_complete = False
            else:
                total_tokens += seed.max_tokens
                if seed.max_tokens > policy.max_research_tokens_per_seed:
                    diagnostics.append(f"seed {seed.task_id} token ceiling exceeds policy")
                if seed.max_tokens > self.limits.max_research_tokens_per_seed:
                    diagnostics.append(f"seed {seed.task_id} token ceiling exceeds system limit")
            if seed.max_cost_usd is None:
                cost_ceiling_complete = False
            else:
                total_cost += seed.max_cost_usd
                if seed.max_cost_usd > policy.max_research_cost_usd_per_seed:
                    diagnostics.append(f"seed {seed.task_id} cost ceiling exceeds policy")
                if seed.max_cost_usd > self.limits.max_research_cost_usd_per_seed:
                    diagnostics.append(f"seed {seed.task_id} cost ceiling exceeds system limit")

        if run_budget is None:
            return
        if run_budget.max_tokens is not None:
            remaining_tokens = run_budget.max_tokens - run_budget.tokens_used
            if not token_ceiling_complete:
                diagnostics.append("finite Run token budget requires every seed token ceiling")
            elif total_tokens > remaining_tokens:
                diagnostics.append(
                    f"seed token ceiling total {total_tokens} exceeds Run remaining "
                    f"{remaining_tokens}"
                )
        if run_budget.max_cost_usd is not None:
            remaining_cost = run_budget.max_cost_usd - run_budget.cost_usd_used
            if not cost_ceiling_complete:
                diagnostics.append("finite Run cost budget requires every seed cost ceiling")
            elif total_cost > remaining_cost:
                diagnostics.append(
                    f"seed cost ceiling total {total_cost} exceeds Run remaining {remaining_cost}"
                )


class PlanAcceptor:
    """Atomically stores an accepted Plan and materializes Tasks (tasks 5.6/5.7).

    Rejection stores diagnostics without creating any Task (no partial state).
    Call within a UnitOfWork transaction so plan + tasks + dependencies + run +
    event commit together.
    """

    def __init__(self, uow, clock, idgen) -> None:
        self.uow = uow
        self.clock = clock
        self.idgen = idgen

    def accept(
        self, run: Run, proposal: PlanProposal, validation: PlanValidation
    ) -> tuple[Plan, Run]:
        now = self.clock.now()
        next_version = (run.current_plan_version or 0) + 1

        if not validation.accepted or validation.graph is None:
            rejected = Plan(
                run_id=run.run_id,
                graph=proposal.to_graph(next_version),
                proposed_at=now,
            ).reject(validation.diagnostics, now)
            self.uow.plans.save(rejected)
            self._emit(
                run, EffectType.PLAN_REJECTED, now, {"diagnostics": list(validation.diagnostics)}
            )
            return rejected, run

        graph = validation.graph.model_copy(update={"version": next_version})
        plan = Plan(run_id=run.run_id, graph=graph, proposed_at=now).accept(now)
        self.uow.plans.save(plan)

        tasks = [
            Task(
                task_id=spec.task_id,
                run_id=run.run_id,
                plan_version=next_version,
                worker_role=spec.worker_role,
                state=TaskState.PENDING,
                depth=spec.depth,
                depends_on=spec.depends_on,
                required_capabilities=spec.required_capabilities,
                branch_role=spec.branch_role,
                deliverable_path=spec.deliverable_path,
                description=spec.description,
                created_at=now,
                updated_at=now,
            )
            for spec in graph.task_specs
        ]
        self.uow.tasks.materialize(tasks)
        self.uow.dependencies.save(run.run_id, next_version, list(graph.dependencies))

        target_state = RunState.RESEARCHING if run.state is RunState.PLANNING else run.state
        new_run = run.model_copy(
            update={
                "current_plan_version": next_version,
                "state": target_state,
                "updated_at": now,
                "state_version": run.state_version + 1,
            }
        )
        self.uow.runs.save(new_run, expected_version=run.state_version)
        self._emit(new_run, EffectType.PLAN_ACCEPTED, now, {"plan_version": next_version})
        return plan, new_run

    def _emit(self, run: Run, effect: EffectType, at, payload: dict) -> None:
        self.uow.events.append(
            [
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=effect,
                    state_version=run.state_version,
                    occurred_at=at,
                    plan_version=run.current_plan_version,
                    payload=payload,
                )
            ]
        )
