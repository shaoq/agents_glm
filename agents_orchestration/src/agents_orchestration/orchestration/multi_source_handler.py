"""Multi-source EVIDENCE_RESEARCHER handler (tasks 3.1-3.3).

Replaces the single-source ``_LLMResearchHandler``: fans a research Task out to
its declared capabilities as concurrent Branches (via ``branches.py``), then
joins accepted evidence with ``EvidenceJoiner``. Failed lanes degrade per
``JoinPolicy`` (Degradation disclosed); the Task still SUCCEEDS when required
lanes delivered evidence.

Dispatch goes through the real ``CapabilityRouter`` via the ``invoke`` closure
handed by ``WorkerExecutor``, so capability permissions stay enforced. The handler
adapts the Router's ``invoke(CapabilityRequest)`` to ``dispatch_branches``'
``invoke(capability_kind, request)`` (the kind is redundant — ``request`` already
carries ``capability_id`` for routing).
"""

from __future__ import annotations

from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.enums import BranchRole, CapabilityKind
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.orchestration.branches import (
    Branch,
    EvidenceJoiner,
    JoinPolicy,
    dispatch_branches,
)

# Default BranchRole per capability. MUST stay consistent with the roles in
# llm_ports._SOURCE_HINT_MAP (local_knowledge/personal_context → REQUIRED,
# live_web → OPTIONAL). Centralized here so the handler does not depend on the
# LLM-port module.
_CAPABILITY_BRANCH_ROLE: dict[CapabilityKind, BranchRole] = {
    CapabilityKind.RAG_SEARCH: BranchRole.REQUIRED,
    CapabilityKind.MEMORY_RECALL: BranchRole.REQUIRED,
    CapabilityKind.WEB_RESEARCH: BranchRole.OPTIONAL,
}


class MultiSourceResearchHandler:
    """EVIDENCE_RESEARCHER worker handler: multi-source Branch fan-out + Join."""

    def __init__(self, registry, idgen, *, policy: JoinPolicy | None = None) -> None:
        self.registry = registry
        self.idgen = idgen
        self.policy = policy or JoinPolicy.default()

    async def handle(self, task: Task, attempt: Attempt, run: Run, invoke) -> TaskResult:
        branches = self._build_branches(task, attempt, run)
        if not branches:
            # No usable capability (all unregistered/filtered) — degrade honestly,
            # never fabricate evidence.
            return TaskResult(
                attempt_id=attempt.attempt_id,
                task_id=task.task_id,
                run_id=run.run_id,
                worker_role=task.worker_role,
                summary="no-capability",
            )

        results = await dispatch_branches(branches, _RouterInvoke(invoke))

        accepted: list[Branch] = []
        for branch in branches:
            result = results.get(branch.branch_id)
            if result is not None and result.succeeded:
                accepted.append(branch.accept(result))
            else:
                accepted.append(branch)  # failed lane — EvidenceJoiner degrades

        joined, _degradations = EvidenceJoiner().join(
            run_id=run.run_id,
            task_id=task.task_id,
            branches=tuple(accepted),
            policy=self.policy,
        )

        return TaskResult(
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            run_id=run.run_id,
            worker_role=task.worker_role,
            evidence=joined.evidences,
            summary=f"multi-source:{len(joined.evidences)}",
        )

    def _build_branches(self, task: Task, attempt: Attempt, run: Run) -> list[Branch]:
        query = task.description or run.raw_goal
        branches: list[Branch] = []
        for kind in task.required_capabilities:
            descriptor = self.registry.find_kind(kind)
            if descriptor is None:
                continue  # capability not registered → lane skipped
            role = _CAPABILITY_BRANCH_ROLE.get(kind, BranchRole.OPTIONAL)
            request = CapabilityRequest(
                request_id=self.idgen.new_id("creq"),
                capability_id=descriptor.capability_id,
                worker_id=f"worker::{task.worker_role.value}",
                run_id=run.run_id,
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                inputs={"query": query},
            )
            branches.append(
                Branch(
                    branch_id=f"{task.task_id}:{kind.value}",
                    task_id=task.task_id,
                    role=role,
                    capability_kind=kind,
                    request=request,
                )
            )
        return branches


class _RouterInvoke:
    """Adapt ``WorkerExecutor``'s ``invoke(CapabilityRequest)`` to
    ``dispatch_branches``' expected ``invoke(capability_kind, request)``.

    The capability_kind argument is intentionally ignored: ``request.capability_id``
    is what the Router routes on.
    """

    def __init__(self, invoke) -> None:
        self._invoke = invoke

    async def __call__(self, _kind, request):
        return await self._invoke(request)
