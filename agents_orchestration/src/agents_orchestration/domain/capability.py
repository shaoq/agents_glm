"""Capability descriptor, request and result (design Decision 6/7 / task 2.3).

All Task capabilities use the async ``invoke(CapabilityRequest) -> CapabilityResult``
Port. Results carry status, data/evidence/citation, source, usage, degradation
and structured failure metadata so the runtime never needs to understand
provider-specific semantics.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.enums import FailureCode
from agents_orchestration.domain.evidence import Degradation, Evidence, SourceIdentity, Usage
from agents_orchestration.domain.execution import OutcomeCertainty
from agents_orchestration.domain.ids import AttemptId, CapabilityId, RunId, TaskId, WorkerId


class CapabilityPermission(StrEnum):
    """First release is physically read-only (design Decision 12 / task 12.5)."""

    READ = "read"
    WRITE = "write"  # rejected by the registry in the first release


class CapabilityResultStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CapabilityDescriptor(BaseModel):
    """Static metadata describing a registered capability (task 6.4)."""

    model_config = ConfigDict(frozen=True)

    capability_id: CapabilityId
    version: int = Field(default=1, ge=1)
    kind: str
    permission: CapabilityPermission = CapabilityPermission.READ
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    max_concurrency: int = Field(default=1, ge=1)
    health: HealthState = HealthState.HEALTHY
    description: str | None = None


class CapabilityRequest(BaseModel):
    """A single capability invocation, deduplicated by ``request_id``."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    capability_id: CapabilityId
    worker_id: WorkerId
    run_id: RunId
    task_id: TaskId
    attempt_id: AttemptId
    inputs: dict[str, object] = Field(default_factory=dict)
    deadline_at: datetime | None = None
    data_scope: str | None = None


class CapabilityResult(BaseModel):
    """Normalized capability outcome (design Decision 7)."""

    model_config = ConfigDict(frozen=True)

    status: CapabilityResultStatus
    operation_id: str
    data: dict[str, object] = Field(default_factory=dict)
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)
    citation: str | None = None
    source: SourceIdentity | None = None
    usage: Usage = Field(default_factory=Usage)
    degradation: tuple[Degradation, ...] = Field(default_factory=tuple)
    failure_code: FailureCode | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None
    outcome_certainty: OutcomeCertainty = OutcomeCertainty.CONFIRMED

    @property
    def succeeded(self) -> bool:
        return self.status in {CapabilityResultStatus.OK, CapabilityResultStatus.DEGRADED}

    @property
    def is_degraded(self) -> bool:
        return self.status is CapabilityResultStatus.DEGRADED or bool(self.degradation)

    @property
    def is_terminal_failure(self) -> bool:
        return self.status is CapabilityResultStatus.FAILED and not self.retryable

    @classmethod
    def ok(
        cls,
        *,
        operation_id: str,
        data: dict[str, object] | None = None,
        evidence: tuple[Evidence, ...] = (),
        usage: Usage | None = None,
        source: SourceIdentity | None = None,
        citation: str | None = None,
    ) -> CapabilityResult:
        return cls(
            status=CapabilityResultStatus.OK,
            operation_id=operation_id,
            data=data or {},
            evidence=evidence,
            usage=usage or Usage(),
            source=source,
            citation=citation,
        )

    @classmethod
    def degraded(
        cls,
        *,
        operation_id: str,
        degradation: tuple[Degradation, ...],
        data: dict[str, object] | None = None,
        evidence: tuple[Evidence, ...] = (),
        usage: Usage | None = None,
    ) -> CapabilityResult:
        return cls(
            status=CapabilityResultStatus.DEGRADED,
            operation_id=operation_id,
            data=data or {},
            evidence=evidence,
            usage=usage or Usage(),
            degradation=degradation,
        )

    @classmethod
    def failed(
        cls,
        *,
        operation_id: str,
        failure_code: FailureCode,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        outcome_certainty: OutcomeCertainty = OutcomeCertainty.UNKNOWN,
    ) -> CapabilityResult:
        return cls(
            status=CapabilityResultStatus.FAILED,
            operation_id=operation_id,
            failure_code=failure_code,
            retryable=failure_code.retryable if retryable is None else retryable,
            retry_after_seconds=retry_after_seconds,
            outcome_certainty=outcome_certainty,
        )
