"""Worker definition and TaskResult (design Decision 6 / task 2.3).

A Worker expresses "who acts in which role"; a Capability expresses "what may be
called". Workers receive a Task-scoped context projection and may only emit a
:class:`TaskResult` or a semantic Proposal — they cannot touch runtime
repositories directly (task 6.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.artifact import ArtifactRef
from agents_orchestration.domain.enums import CapabilityKind, WorkerRole
from agents_orchestration.domain.evidence import Degradation, Evidence, Usage
from agents_orchestration.domain.ids import AttemptId, RunId, TaskId, WorkerId
from agents_orchestration.domain.policy import Budget


class WorkerDefinition(BaseModel):
    """Static definition of a worker role (task 6.1)."""

    model_config = ConfigDict(frozen=True)

    worker_id: WorkerId
    role: WorkerRole
    description: str
    allowed_capabilities: tuple[CapabilityKind, ...] = Field(default_factory=tuple)
    input_contract_ref: str | None = None
    output_contract_ref: str | None = None
    prompt_version: str = "v1"
    policy_version: str = "v1"
    per_task_budget: Budget | None = None

    def is_allowed(self, capability: CapabilityKind) -> bool:
        return capability in self.allowed_capabilities


class TaskResult(BaseModel):
    """The accepted output of an Attempt (task 6.2/6.3).

    Workers cannot return arbitrary runtime mutations — only this value object
    plus optional semantic Proposals (defined in Section 5/10).
    """

    model_config = ConfigDict(frozen=True)

    attempt_id: AttemptId
    task_id: TaskId
    run_id: RunId
    worker_role: WorkerRole
    artifacts: tuple[ArtifactRef, ...] = Field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)
    usage: Usage = Field(default_factory=Usage)
    summary: str = ""
    degradation: tuple[Degradation, ...] = Field(default_factory=tuple)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
