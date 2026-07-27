"""Domain Events for every formal transition (design Decision 4 / task 2.9).

Events are immutable, carry the State Version they were emitted against, and are
persisted in the same transaction as the state transition (task 3.6) so that
restart recovery, audit and ``run watch`` all observe a consistent causal order.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agents_orchestration.domain.enums import EffectType
from agents_orchestration.domain.ids import EventId, RunId


class DomainEvent(BaseModel):
    """A single formal transition, recorded atomically with state."""

    model_config = ConfigDict(frozen=True)

    event_id: EventId
    run_id: RunId
    effect: EffectType
    state_version: int
    occurred_at: datetime
    task_id: str | None = None
    attempt_id: str | None = None
    plan_version: int | None = None
    gate_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.effect in {
            EffectType.RUN_TERMINATED,
            EffectType.RUN_CANCELED,
        }
