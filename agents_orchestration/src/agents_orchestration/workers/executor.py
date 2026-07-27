"""WorkerExecutor: runs one Attempt through a worker handler (tasks 6.2 / 6.3).

The executor is the runtime's :class:`TaskExecutor` (Section 4). It projects a
Task-scoped context, hands the worker a bounded ``invoke`` closure routed through
the :class:`CapabilityRouter`, and validates that the worker emits a
:class:`TaskResult` (never a runtime mutation) (task 6.3). Because every
capability call goes through the router, Planner output and untrusted evidence
cannot expand capability permissions (task 6.9).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from agents_orchestration.domain.capability import CapabilityRequest, CapabilityResult
from agents_orchestration.domain.enums import FailureCode, WorkerRole
from agents_orchestration.domain.execution import Attempt, Run, Task
from agents_orchestration.domain.worker import TaskResult
from agents_orchestration.runtime.tick import TaskExecutionOutcome

Invoke = Callable[[CapabilityRequest], Awaitable[CapabilityResult]]


class WorkerFailure(RuntimeError):
    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class WorkerHandler(Protocol):
    """Role-specific worker logic. Returns a TaskResult or raises WorkerFailure."""

    async def handle(
        self, task: Task, attempt: Attempt, run: Run, invoke: Invoke
    ) -> TaskResult: ...


class WorkerExecutor:
    def __init__(
        self,
        workers,
        router,
        handlers: dict[WorkerRole, WorkerHandler],
        run_policy,
    ) -> None:
        self.workers = workers
        self.router = router
        self.handlers = handlers
        self.run_policy = run_policy

    async def execute(self, task: Task, attempt: Attempt, run: Run) -> TaskExecutionOutcome:
        definition = self.workers.get(task.worker_role)
        if definition is None:
            return TaskExecutionOutcome(False, failure_code=FailureCode.UNKNOWN)
        handler = self.handlers.get(task.worker_role)
        if handler is None:
            return TaskExecutionOutcome(False, failure_code=FailureCode.UNKNOWN)

        async def invoke(request: CapabilityRequest) -> CapabilityResult:
            return await self.router.invoke(request, worker=definition, run_policy=self.run_policy)

        try:
            result = await handler.handle(task, attempt, run, invoke)
        except WorkerFailure as failure:
            return TaskExecutionOutcome(False, failure_code=failure.code)
        # Task 6.3: a worker may only emit a TaskResult.
        if not isinstance(result, TaskResult):
            return TaskExecutionOutcome(False, failure_code=FailureCode.INVALID_RESPONSE)
        return TaskExecutionOutcome(True, task_result=result, usage=result.usage)
