"""In-memory overlay used while a write batch is planned but not committed."""

from dataclasses import dataclass, field

from agents_memory.models import (
    Action,
    ActionPlan,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    PendingResolution,
    Validity,
)


@dataclass
class WriteBatchState:
    """Keep later candidates consistent with earlier plans in the same batch.

    This state is deliberately non-persistent. SQLite receives the complete
    ordered plan only after relation decisions for the batch have succeeded.
    """

    plans: list[ActionPlan] = field(default_factory=list)
    embeddings: dict[int, list[float]] = field(default_factory=dict)
    staged_memories: list[MemoryRecord] = field(default_factory=list)
    inactive_ids: set[str] = field(default_factory=set)
    deferred_groups: list[PendingResolution] = field(default_factory=list)

    def record(
        self,
        plan: ActionPlan,
        *,
        embedding: list[float] | None = None,
    ) -> None:
        """Append a plan and retain its already-computed candidate embedding."""

        self.plans.append(plan)
        if embedding is not None:
            self.embeddings[plan.candidate_index] = embedding

    def stage_materialized(
        self,
        plan: ActionPlan,
        *,
        scope: MemoryScope,
        memory_id: str,
    ) -> ActionPlan:
        """Apply an ADD/UPDATE to the overlay and return the ID-bearing plan."""

        if plan.action not in (Action.ADD, Action.UPDATE):
            raise ValueError("only ADD or UPDATE plans materialize memories")
        if plan.action is Action.UPDATE:
            self.inactive_ids.update(plan.target_ids)
            self.staged_memories = [
                item for item in self.staged_memories if item.id not in plan.target_ids
            ]

        staged_plan = plan.model_copy(update={"new_memory_id": memory_id})
        candidate = plan.candidate
        self.staged_memories.append(
            MemoryRecord(
                id=memory_id,
                scope=scope,
                type=candidate.type,
                content=candidate.content,
                importance=candidate.importance,
                confidence=candidate.confidence,
                validity=Validity.ACTIVE,
                event_frame=candidate.event_frame,
                metadata=candidate.metadata,
            )
        )
        return staged_plan

    def visible_histories(
        self,
        stored: list[MemoryRecord],
        memory_type: MemoryType,
    ) -> list[MemoryRecord]:
        """Overlay planned changes on lookup results for the next candidate."""

        return [item for item in stored if item.id not in self.inactive_ids] + [
            item for item in self.staged_memories if item.type is memory_type
        ]
