"""Tests for DeterministicPlanner: lanes, narrowing, quotas and limits
(task 3.5/3.6).
"""

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    QueryVariant,
    RecallIntent,
    RecallLane,
    RecallRequest,
)
from agents_memory.recall.planning import PlannerConfig, DeterministicPlanner


def _intent() -> RecallIntent:
    return RecallIntent(
        primary_query="q",
        query_variants=(QueryVariant(text="q", purpose="original"),),
    )


def _request(**kwargs) -> RecallRequest:
    defaults: dict = {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "query": "q"}
    defaults.update(kwargs)
    return RecallRequest(**defaults)


def _lane_by_lane(plan, lane: RecallLane):
    return next((lp for lp in plan.lanes if lp.lane is lane), None)


class TestPlannerLanes:
    def test_three_lanes_when_all_allowed(self):
        plan = DeterministicPlanner().plan(_request(), _intent(), RecallDiagnostics())
        lanes = {lp.lane for lp in plan.lanes}
        assert lanes == {
            RecallLane.SESSION_CURRENT,
            RecallLane.AGENT_HISTORY,
            RecallLane.USER_SHARED,
        }

    def test_caller_disables_agent_history(self):
        plan = DeterministicPlanner().plan(
            _request(allow_agent_history=False), _intent(), RecallDiagnostics()
        )
        assert _lane_by_lane(plan, RecallLane.AGENT_HISTORY) is None

    def test_caller_disables_user_shared(self):
        plan = DeterministicPlanner().plan(
            _request(allow_user_shared=False), _intent(), RecallDiagnostics()
        )
        assert _lane_by_lane(plan, RecallLane.USER_SHARED) is None

    def test_session_lane_requires_session_id(self):
        plan = DeterministicPlanner().plan(
            RecallRequest(user_id="u1", agent_id="a1", query="q"),
            _intent(),
            RecallDiagnostics(),
        )
        assert _lane_by_lane(plan, RecallLane.SESSION_CURRENT) is None

    def test_agent_history_lane_requires_agent_id(self):
        plan = DeterministicPlanner().plan(
            RecallRequest(user_id="u1", session_id="s1", query="q"),
            _intent(),
            RecallDiagnostics(),
        )
        assert _lane_by_lane(plan, RecallLane.AGENT_HISTORY) is None


class TestPlannerUserBoundary:
    def test_all_lanes_share_request_user_id(self):
        plan = DeterministicPlanner().plan(_request(), _intent(), RecallDiagnostics())
        for lane in plan.lanes:
            assert lane.scope.user_id == "u1"

    def test_lanes_cannot_widen_to_other_user(self):
        plan = DeterministicPlanner().plan(
            _request(allow_user_shared=True), _intent(), RecallDiagnostics()
        )
        shared = _lane_by_lane(plan, RecallLane.USER_SHARED)
        assert shared is not None
        assert shared.scope.agent_id is None
        assert shared.scope.session_id is None
        assert shared.scope.user_id == "u1"

    def test_session_lane_scope_is_precise(self):
        plan = DeterministicPlanner().plan(_request(), _intent(), RecallDiagnostics())
        session = _lane_by_lane(plan, RecallLane.SESSION_CURRENT)
        assert session.scope.agent_id == "a1"
        assert session.scope.session_id == "s1"


class TestPlannerBudgets:
    def test_quotas_match_config(self):
        config = PlannerConfig(
            session_quota=5,
            agent_history_quota=7,
            user_shared_quota=9,
            global_candidate_limit=21,
        )
        plan = DeterministicPlanner(config).plan(_request(), _intent(), RecallDiagnostics())
        assert _lane_by_lane(plan, RecallLane.SESSION_CURRENT).candidate_quota == 5
        assert _lane_by_lane(plan, RecallLane.AGENT_HISTORY).candidate_quota == 7
        assert _lane_by_lane(plan, RecallLane.USER_SHARED).candidate_quota == 9
        assert plan.global_candidate_limit == 21

    def test_global_limit_capped_by_config(self):
        plan = DeterministicPlanner().plan(_request(), _intent(), RecallDiagnostics())
        assert plan.global_candidate_limit == PlannerConfig().global_candidate_limit

    def test_lanes_carry_intent_types_and_variants(self):
        plan = DeterministicPlanner().plan(_request(), _intent(), RecallDiagnostics())
        for lane in plan.lanes:
            assert lane.target_types == _intent().target_memory_types
            assert len(lane.query_variants) >= 1
