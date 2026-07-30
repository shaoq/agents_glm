"""Bounded Replan: preserve accepted work, supersede invalidated tasks, add
focused tasks under a new Plan Version (task 5.9).

Replan, Retry and Report revision share the Run budget and never reset the
deadline (design Decision 10). Preserved Tasks keep their state and accepted
result; only invalidated Tasks are SUPERSEDED.
"""

from __future__ import annotations

from agents_orchestration.domain.enums import EffectType, TaskState, WorkerRole
from agents_orchestration.domain.events import DomainEvent
from agents_orchestration.domain.execution import Run, Task
from agents_orchestration.domain.plan import Dependency, Plan, PlanGraph, TaskSpec
from agents_orchestration.orchestration.planner import PlanAcceptor, PlanValidator
from agents_orchestration.orchestration.proposals import ReplanProposal


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
        current = self.uow.plans.current(run.run_id)
        if current is None:
            raise ValueError(f"Run {run.run_id} has no plan to replan")
        next_version = current.version + 1
        invalidate = set(proposal.invalidate_task_ids)
        current_tasks = self.uow.tasks.by_run(run.run_id, current.version)

        for spec in proposal.add_task_specs:
            if spec.worker_role is not WorkerRole.EVIDENCE_RESEARCHER:
                raise ValueError(
                    f"Replan cannot add non-research task {spec.task_id} "
                    f"(role {spec.worker_role.value})"
                )

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
            # Promote preserved task to the new version; keep state and result.
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
        all_deps: list[Dependency] = carried_deps + list(proposal.add_dependencies)
        self.uow.dependencies.save(run.run_id, next_version, all_deps)

        all_specs: tuple[TaskSpec, ...] = tuple(self._to_spec(t) for t in preserved) + tuple(
            proposal.add_task_specs
        )
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
