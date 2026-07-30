"""Bounded Replan: preserve accepted work, supersede invalidated tasks, add
focused tasks under a new Plan Version (task 5.9).

Replan, Retry and Report revision share the Run budget and never reset the
deadline (design Decision 10). Preserved Tasks keep their state and accepted
result; only invalidated Tasks are SUPERSEDED.

``replan_and_transition`` (analyze-sufficiency-feedback Decision 4 / tasks 4.1-4.3)
is the atomic phase-facing entry point: it validates role/capability/dependency/
budget invariants BEFORE the first write, then commits Plan v+1, promoted
preserved Tasks, the new PENDING research Task(s), the Run transition to
RESEARCHING (``current_plan_version``/``replan_count``/``state_version``), and
the ``PLAN_REPLANNED`` + ``RUN_STATE_TRANSITION`` events in ONE transaction.
"""

from __future__ import annotations

from agents_orchestration.domain.enums import EffectType, RunState, TaskState, WorkerRole
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.plan import Dependency, Plan, PlanGraph, TaskSpec
from agents_orchestration.domain.state_machine import assert_run_transition
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import PlanProposal, ReplanProposal


class ReplanBudgetExhausted(RuntimeError):
    """Raised when a focused replan is requested with no replan budget left."""


class ReplanService:
    def __init__(
        self,
        uow,
        validator: PlanValidator,
        acceptor: PlanAcceptor,
        clock,
        idgen,
    ) -> None:
        self.uow = uow
        self.validator = validator
        self.acceptor = acceptor
        self.clock = clock
        self.idgen = idgen

    def replan(
        self,
        run: Run,
        proposal: ReplanProposal,
    ) -> tuple[Plan, Run]:
        now = self.clock.now()
        plan, preserved_ids, invalidate, next_version, _added = self._persist_replan_graph(
            run, proposal, now
        )
        new_run = run.model_copy(
            update={
                "current_plan_version": next_version,
                "replan_count": run.replan_count + 1,
                "updated_at": now,
                "state_version": run.state_version + 1,
            }
        )
        self.uow.runs.save(new_run, expected_version=run.state_version)
        self.uow.events.append(
            [
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.PLAN_REPLANNED,
                    state_version=new_run.state_version,
                    occurred_at=now,
                    plan_version=next_version,
                    payload={
                        "invalidated": sorted(invalidate),
                        "added": [s.task_id for s in proposal.add_task_specs],
                        "preserved": sorted(preserved_ids),
                    },
                )
            ]
        )
        return plan, new_run

    def replan_and_transition(
        self,
        run: Run,
        proposal: ReplanProposal,
        *,
        transition_to: RunState,
        correlation: dict | None = None,
        now=None,
    ) -> tuple[Plan, Run]:
        """Atomic focused replan + Run state transition (tasks 4.1-4.3).

        All validation (research-only roles, ≥1 new PENDING Task, remaining
        budget, legal state transition) happens BEFORE the first repository
        write. Plan v+1, promoted preserved Tasks, new Tasks, dependencies, the
        Run transition (RESEARCHING + plan version + replan counter + state
        version) and both events are committed in the caller's transaction; any
        failure rolls back every write.
        """

        timestamp = now or self.clock.now()
        self._require_transition(run.state, transition_to)
        if not proposal.add_task_specs:
            raise ValueError("focused replan must add at least one PENDING research task")
        if run.replan_count >= run.policy.max_replans:
            raise ReplanBudgetExhausted(
                f"replan budget exhausted ({run.replan_count}/{run.policy.max_replans})"
            )
        self._validate_candidate(run, proposal)

        plan, preserved_ids, invalidate, next_version, added_ids = self._persist_replan_graph(
            run, proposal, timestamp
        )

        moved = run.transition(transition_to, timestamp)  # asserts legality, bumps state_version
        new_run = moved.model_copy(
            update={
                "current_plan_version": next_version,
                "replan_count": run.replan_count + 1,
            }
        )
        self.uow.runs.save(new_run, expected_version=run.state_version)

        payload: dict = {
            "invalidated": sorted(invalidate),
            "added": added_ids,
            "preserved": sorted(preserved_ids),
            "old_plan_version": next_version - 1,
            "new_plan_version": next_version,
        }
        if correlation:
            payload.update(correlation)
        self.uow.events.append(
            [
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.PLAN_REPLANNED,
                    state_version=new_run.state_version,
                    occurred_at=timestamp,
                    plan_version=next_version,
                    payload=payload,
                ),
                DomainEvent(
                    event_id=self.idgen.new_id("evt"),
                    run_id=run.run_id,
                    effect=EffectType.RUN_STATE_TRANSITION,
                    state_version=new_run.state_version,
                    occurred_at=timestamp,
                    plan_version=next_version,
                    payload={
                        "phase": "focused_replan",
                        "from": run.state.value,
                        "to": transition_to.value,
                    },
                ),
            ]
        )
        return plan, new_run

    def _validate_candidate(self, run: Run, proposal: ReplanProposal) -> None:
        """Validate the complete Plan v+1 graph before the first repository write."""

        if proposal.run_id != run.run_id:
            raise ValueError(
                f"replan run_id {proposal.run_id} does not match Run {run.run_id}"
            )
        current = self.uow.plans.current(run.run_id)
        if current is None:
            raise ValueError(f"Run {run.run_id} has no plan to replan")

        invalidate = set(proposal.invalidate_task_ids)
        current_tasks = self.uow.tasks.by_run(run.run_id, current.version)
        preserved_specs = tuple(
            self._to_spec(task)
            for task in current_tasks
            if task.task_id not in invalidate
            and task.worker_role is WorkerRole.EVIDENCE_RESEARCHER
        )
        candidate_specs = preserved_specs + tuple(proposal.add_task_specs)
        task_ids = [spec.task_id for spec in candidate_specs]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("focused replan contains duplicate task ids")

        preserved_ids = {spec.task_id for spec in preserved_specs}
        carried_dependencies = tuple(
            dep
            for dep in self.uow.dependencies.by_plan(run.run_id, current.version)
            if dep.predecessor in preserved_ids and dep.successor in preserved_ids
        )
        candidate_dependencies = self._merge_dependencies(
            carried_dependencies,
            tuple(proposal.add_dependencies),
            self._depends_on_edges(candidate_specs),
        )
        candidate = PlanProposal(
            run_id=run.run_id,
            plan_id=current.graph.plan_id,
            task_specs=candidate_specs,
            dependencies=candidate_dependencies,
            deliverable_paths=current.graph.deliverable_paths,
        )
        completion = run.completion or self.uow.completion.get(run.run_id)
        if completion is None:
            raise ValueError(f"Run {run.run_id} has no CompletionContract")
        approved_capabilities = frozenset(
            capability
            for spec in current.graph.task_specs
            for capability in spec.required_capabilities
        )
        validation = self.validator.validate(
            candidate,
            policy=run.policy,
            allowed_capabilities=approved_capabilities,
            completion=completion,
            version=current.version + 1,
        )
        if not validation.accepted:
            raise ValueError(
                "invalid focused replan: " + "; ".join(validation.diagnostics)
            )

    # --- shared plan persistence (no Run/event side effects) ----------------

    def _persist_replan_graph(
        self, run: Run, proposal: ReplanProposal, now
    ) -> tuple[Plan, set[str], set[str], int, list[str]]:
        """Promote preserved research Tasks, supersede invalidated ones, add the
        new PENDING Tasks and commit Plan v+1. Returns the new Plan, preserved
        ids, invalidated ids, the new version and the added task ids. Performs
        NO Run write and emits NO event."""

        current = self.uow.plans.current(run.run_id)
        if current is None:
            raise ValueError(f"Run {run.run_id} has no plan to replan")
        next_version = current.version + 1
        invalidate = set(proposal.invalidate_task_ids)

        for spec in proposal.add_task_specs:
            if spec.worker_role is not WorkerRole.EVIDENCE_RESEARCHER:
                raise ValueError(
                    f"Replan cannot add non-research task {spec.task_id} "
                    f"(role {spec.worker_role.value})"
                )

        current_tasks = self.uow.tasks.by_run(run.run_id, current.version)
        preserved: list[Task] = []
        for task in current_tasks:
            if task.task_id in invalidate:
                if not task.is_terminal:
                    self.uow.tasks.save(task.transition(TaskState.SUPERSEDED, now))
                continue
            # Replan does not carry non-research Tasks into the new version
            # (remove-noop-phase-tasks 1.4); only evidence_researcher is dispatchable.
            if task.worker_role is not WorkerRole.EVIDENCE_RESEARCHER:
                continue
            promoted = task.model_copy(
                update={
                    "plan_version": next_version,
                    "updated_at": now,
                    "state_version": task.state_version + 1,
                }
            )
            self.uow.tasks.save(promoted)
            preserved.append(promoted)

        for spec in proposal.add_task_specs:
            self.uow.tasks.save(
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
            )

        preserved_ids = {t.task_id for t in preserved}
        carried_deps = [
            d
            for d in self.uow.dependencies.by_plan(run.run_id, current.version)
            if d.predecessor in preserved_ids and d.successor in preserved_ids
        ]
        all_specs: tuple[TaskSpec, ...] = tuple(self._to_spec(t) for t in preserved) + tuple(
            proposal.add_task_specs
        )
        all_deps = self._merge_dependencies(
            tuple(carried_deps),
            tuple(proposal.add_dependencies),
            self._depends_on_edges(all_specs),
        )
        self.uow.dependencies.save(run.run_id, next_version, list(all_deps))

        graph = PlanGraph(
            plan_id=current.graph.plan_id,
            version=next_version,
            task_specs=all_specs,
            dependencies=tuple(all_deps),
            deliverable_paths=current.graph.deliverable_paths,
        )
        plan = Plan(run_id=run.run_id, graph=graph, proposed_at=now).accept(now)
        self.uow.plans.save(plan)
        self.uow.plans.save(current.supersede(next_version))
        added_ids = [s.task_id for s in proposal.add_task_specs]
        return plan, preserved_ids, invalidate, next_version, added_ids

    @staticmethod
    def _depends_on_edges(specs: tuple[TaskSpec, ...]) -> tuple[Dependency, ...]:
        return tuple(
            Dependency(predecessor=predecessor, successor=spec.task_id)
            for spec in specs
            for predecessor in spec.depends_on
        )

    @staticmethod
    def _merge_dependencies(
        *groups: tuple[Dependency, ...],
    ) -> tuple[Dependency, ...]:
        merged: dict[tuple[str, str, object], Dependency] = {}
        for dependency in (dep for group in groups for dep in group):
            key = (
                dependency.predecessor,
                dependency.successor,
                dependency.kind,
            )
            merged.setdefault(key, dependency)
        return tuple(merged.values())

    @staticmethod
    def _require_transition(current: RunState, target: RunState) -> None:
        if target is None:
            raise ValueError("transition_to is required for replan_and_transition")
        assert_run_transition(current, target)

    @staticmethod
    def _to_spec(task: Task) -> TaskSpec:
        return TaskSpec(
            task_id=task.task_id,
            worker_role=task.worker_role,
            description=task.deliverable_path or task.task_id,
            required_capabilities=task.required_capabilities,
            branch_role=task.branch_role,
            deliverable_path=task.deliverable_path,
            depth=task.depth,
            depends_on=task.depends_on,
        )


__all__ = [
    "ReplanBudgetExhausted",
    "ReplanService",
]
