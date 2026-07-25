"""Deterministic Recall planning: authorization-aware lanes and budgets.

The planner never consults an LLM. It derives three candidate lanes
(session_current, agent_history, user_shared) from the request's identity and
the caller's allow flags, binds each to the same ``user_id``, assigns bounded
quotas, and exposes a global candidate limit. A caller may narrow scope but
can never widen it beyond the user boundary.

Reference: design 5.5 (three lanes) and 13.2 (planning module).
"""

from dataclasses import dataclass

from agents_memory.models import MemoryScope
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    LanePlan,
    RecallIntent,
    RecallLane,
    RecallPlan,
    RecallRequest,
)


@dataclass(frozen=True)
class PlannerConfig:
    session_quota: int = 10
    agent_history_quota: int = 10
    user_shared_quota: int = 10
    global_candidate_limit: int = 30
    max_variants_per_lane: int = 3
    relation_expansion_depth: int = 1


class DeterministicPlanner:
    """Builds a deterministic, user-bound multi-lane RecallPlan."""

    def __init__(self, config: PlannerConfig | None = None) -> None:
        self.config = config or PlannerConfig()

    def plan(
        self,
        request: RecallRequest,
        intent: RecallIntent,
        diag: RecallDiagnostics,  # noqa: ARG002 (accepted for stage protocol symmetry)
    ) -> RecallPlan:
        lanes: list[LanePlan] = []
        if request.session_id and request.agent_id:
            lanes.append(
                self._lane(
                    RecallLane.SESSION_CURRENT,
                    MemoryScope(
                        user_id=request.user_id,
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                    ),
                    self.config.session_quota,
                    intent,
                )
            )
        if request.allow_agent_history and request.agent_id:
            lanes.append(
                self._lane(
                    RecallLane.AGENT_HISTORY,
                    MemoryScope(user_id=request.user_id, agent_id=request.agent_id),
                    self.config.agent_history_quota,
                    intent,
                )
            )
        if request.allow_user_shared:
            lanes.append(
                self._lane(
                    RecallLane.USER_SHARED,
                    MemoryScope(user_id=request.user_id),
                    self.config.user_shared_quota,
                    intent,
                )
            )
        return RecallPlan(
            intent=intent,
            lanes=tuple(lanes),
            global_candidate_limit=self.config.global_candidate_limit,
            relation_expansion_depth=self.config.relation_expansion_depth,
        )

    def _lane(
        self,
        lane: RecallLane,
        scope: MemoryScope,
        quota: int,
        intent: RecallIntent,
    ) -> LanePlan:
        return LanePlan(
            lane=lane,
            enabled=True,
            scope=scope,
            query_variants=intent.query_variants[: self.config.max_variants_per_lane],
            target_types=intent.target_memory_types,
            candidate_quota=quota,
            temporal_need=intent.temporal_need,
            relation_expansion=intent.relationship_need,
        )
