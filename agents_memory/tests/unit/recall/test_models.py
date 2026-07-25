"""Contract tests for Recall public models and enums.

These tests pin the public Recall API surface (RecallRequest, RecallResult,
metadata, enums) and its validation invariants before any pipeline behavior
exists. They are the RED starting point for task 1.2 of the
add-memory-recall-pipeline change.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents_memory.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Message,
    TemporalAnchor,
)
from agents_memory.recall.models import (
    DegradationCode,
    EligibleCandidate,
    EvidenceGroup,
    EvidenceItem,
    EvidenceRole,
    ExecutionStatus,
    ExplicitConstraints,
    HitSignal,
    LanePlan,
    QueryVariant,
    RecallErrorCode,
    RecallIntent,
    RecallLane,
    RecallMetadata,
    RecallPlan,
    RecallRequest,
    RecallResult,
    RejectedCandidate,
    RejectionReason,
    ScoreComponent,
    ScoredCandidate,
    Sufficiency,
    TemporalIntent,
)


def _message(message_id: str = "m1", role: str = "user") -> Message:
    return Message(
        message_id=message_id,
        role=role,
        content="hello",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestRecallEnums:
    def test_recall_lane_covers_three_layers(self):
        assert set(RecallLane) == {
            RecallLane.SESSION_CURRENT,
            RecallLane.AGENT_HISTORY,
            RecallLane.USER_SHARED,
        }

    def test_temporal_intent_covers_query_modes(self):
        assert {
            TemporalIntent.CURRENT_STATE,
            TemporalIntent.POINT_IN_TIME,
            TemporalIntent.INTERVAL,
            TemporalIntent.EVOLUTION,
        }.issubset(set(TemporalIntent))

    def test_evidence_role_members(self):
        expected = {
            EvidenceRole.CURRENT,
            EvidenceRole.HISTORICAL,
            EvidenceRole.EVOLVED,
            EvidenceRole.CORRECTED,
            EvidenceRole.SUPERSEDED,
            EvidenceRole.SUPPORTING,
            EvidenceRole.CONFLICTING,
            EvidenceRole.INDEPENDENT,
            EvidenceRole.UNKNOWN_EVENT_IDENTITY,
        }
        assert expected.issubset(set(EvidenceRole))

    def test_sufficiency_members(self):
        assert set(Sufficiency) == {
            Sufficiency.SUFFICIENT,
            Sufficiency.PARTIAL,
            Sufficiency.CONFLICTED,
            Sufficiency.EMPTY,
        }

    def test_execution_status_members(self):
        assert set(ExecutionStatus) == {
            ExecutionStatus.COMPLETE,
            ExecutionStatus.DEGRADED,
        }

    def test_degradation_code_matches_matrix(self):
        expected = {
            "intent_fallback",
            "query_rewrite_fallback",
            "semantic_unavailable",
            "vector_index_unavailable",
            "partial_lane_failure",
            "scoring_fallback",
            "resolution_fallback",
            "token_estimation_fallback",
            "incomplete_relation_chain",
        }
        assert {code.value for code in DegradationCode} == expected

    def test_rejection_reason_covers_hard_filters(self):
        expected = {
            "wrong_user",
            "unauthorized_scope",
            "invalid_state",
            "explicit_type_violation",
            "hard_time_violation",
            "record_missing",
            "corrupt_record",
            "domain_constraint_violation",
        }
        assert {reason.value for reason in RejectionReason} == expected

    def test_recall_error_code_covers_fatal_failures(self):
        expected = {
            "request_invalid",
            "storage_unavailable",
            "authorization_unavailable",
            "record_load_failed",
            "contract_violation",
            "output_schema_invalid",
            "concurrent_modification",
        }
        assert {code.value for code in RecallErrorCode} == expected


class TestRecallRequestConstruction:
    def test_minimal_valid_request(self):
        request = RecallRequest(user_id="u1", query="what did I decide")
        assert request.user_id == "u1"
        assert request.agent_id is None
        assert request.session_id is None
        assert request.recent_messages == ()
        assert request.allow_agent_history is True
        assert request.allow_user_shared is True
        assert request.diagnostic is False

    def test_full_request_preserves_fields(self):
        request = RecallRequest(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            query="current status",
            recent_messages=(_message("m1"), _message("m2", "assistant")),
            purpose="recover decision",
            explicit_types=(MemoryType.FACT, MemoryType.EVENT),
            temporal_intent=TemporalIntent.POINT_IN_TIME,
            allow_agent_history=False,
            allow_user_shared=False,
            max_evidence_items=5,
            max_context_tokens=1000,
            diagnostic=True,
        )
        assert request.agent_id == "a1"
        assert request.session_id == "s1"
        assert request.purpose == "recover decision"
        assert request.explicit_types == (MemoryType.FACT, MemoryType.EVENT)
        assert request.allow_agent_history is False
        assert request.max_evidence_items == 5


class TestRecallRequestValidation:
    def test_missing_user_id_rejected(self):
        with pytest.raises(ValidationError):
            RecallRequest(user_id="", query="q")

    def test_blank_query_rejected(self):
        with pytest.raises(ValidationError):
            RecallRequest(user_id="u1", query="   ")

    def test_recent_messages_bounded(self):
        too_many = tuple(_message(f"m{i}") for i in range(RecallRequest.MAX_RECENT_MESSAGES + 1))
        with pytest.raises(ValidationError):
            RecallRequest(user_id="u1", query="q", recent_messages=too_many)

    def test_non_positive_evidence_budget_rejected(self):
        with pytest.raises(ValidationError):
            RecallRequest(user_id="u1", query="q", max_evidence_items=0)

    def test_non_positive_token_budget_rejected(self):
        with pytest.raises(ValidationError):
            RecallRequest(user_id="u1", query="q", max_context_tokens=0)

    def test_evidence_budget_capped_by_system_limit(self):
        with pytest.raises(ValidationError):
            RecallRequest(
                user_id="u1",
                query="q",
                max_evidence_items=RecallRequest.MAX_EVIDENCE_ITEMS_LIMIT + 1,
            )

    def test_token_budget_capped_by_system_limit(self):
        with pytest.raises(ValidationError):
            RecallRequest(
                user_id="u1",
                query="q",
                max_context_tokens=RecallRequest.MAX_CONTEXT_TOKENS_LIMIT + 1,
            )

    def test_invalid_explicit_time_range_rejected(self):
        with pytest.raises(ValidationError):
            RecallRequest(
                user_id="u1",
                query="q",
                explicit_time_range=TemporalAnchor(
                    start=datetime(2026, 2, 1, tzinfo=UTC),
                    end=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            )


class TestRecallRequestImmutability:
    def test_request_is_frozen(self):
        request = RecallRequest(user_id="u1", query="q")
        with pytest.raises(ValidationError):
            request.query = "other"  # type: ignore[misc]


class TestRecallResult:
    def test_result_holds_context_evidence_metadata(self):
        metadata = RecallMetadata(
            intent_summary="need current decision",
            lanes_used=(RecallLane.SESSION_CURRENT,),
            candidate_count=3,
            filtered_count=1,
            final_count=2,
            sufficiency=Sufficiency.SUFFICIENT,
            execution_status=ExecutionStatus.COMPLETE,
        )
        result = RecallResult(context="ctx", evidence=(), metadata=metadata)
        assert result.context == "ctx"
        assert result.evidence == ()
        assert result.metadata.sufficiency is Sufficiency.SUFFICIENT
        assert result.metadata.execution_status is ExecutionStatus.COMPLETE

    def test_empty_result_pattern(self):
        metadata = RecallMetadata(
            candidate_count=0,
            filtered_count=0,
            final_count=0,
            sufficiency=Sufficiency.EMPTY,
            execution_status=ExecutionStatus.COMPLETE,
        )
        result = RecallResult(context="", evidence=(), metadata=metadata)
        assert result.context == ""
        assert result.evidence == ()
        assert result.metadata.sufficiency is Sufficiency.EMPTY
        assert result.metadata.execution_status is ExecutionStatus.COMPLETE

    def test_result_is_frozen(self):
        metadata = RecallMetadata(
            sufficiency=Sufficiency.EMPTY,
            execution_status=ExecutionStatus.COMPLETE,
        )
        result = RecallResult(context="", evidence=(), metadata=metadata)
        with pytest.raises(ValidationError):
            result.context = "x"  # type: ignore[misc]


class TestRecallMetadataDefaults:
    def test_default_degradations_and_truncation(self):
        metadata = RecallMetadata(
            sufficiency=Sufficiency.EMPTY,
            execution_status=ExecutionStatus.COMPLETE,
        )
        assert metadata.degradations == ()
        assert metadata.budget_truncation is False
        assert metadata.lanes_used == ()
        assert metadata.pipeline_version

    def test_metadata_records_degradation(self):
        metadata = RecallMetadata(
            sufficiency=Sufficiency.PARTIAL,
            execution_status=ExecutionStatus.DEGRADED,
            degradations=(DegradationCode.SCORING_FALLBACK,),
        )
        assert DegradationCode.SCORING_FALLBACK in metadata.degradations
        assert metadata.execution_status is ExecutionStatus.DEGRADED


def _record(
    memory_id: str = "m1",
    user_id: str = "u1",
    type_: MemoryType = MemoryType.FACT,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id=user_id),
        type=type_,
        content="sample memory",
        importance=5,
        confidence=0.8,
    )


class TestStageContractSerialization:
    def test_intent_roundtrip(self):
        intent = RecallIntent(
            primary_query="decide",
            query_variants=(QueryVariant(text="decision", purpose="core"),),
            target_memory_types=(MemoryType.FACT,),
            explicit_constraints=ExplicitConstraints(types=(MemoryType.FACT,)),
            confidence=0.7,
        )
        assert RecallIntent.model_validate(intent.model_dump()) == intent

    def test_plan_roundtrip(self):
        plan = RecallPlan(
            intent=RecallIntent(primary_query="q"),
            lanes=(
                LanePlan(
                    lane=RecallLane.SESSION_CURRENT,
                    enabled=True,
                    scope=MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
                    candidate_quota=10,
                ),
                LanePlan(
                    lane=RecallLane.AGENT_HISTORY,
                    enabled=False,
                    scope=MemoryScope(user_id="u1", agent_id="a1"),
                    candidate_quota=0,
                ),
            ),
            global_candidate_limit=20,
        )
        assert RecallPlan.model_validate(plan.model_dump()) == plan

    def test_eligible_candidate_roundtrip(self):
        record = _record()
        candidate = EligibleCandidate(
            memory_id=record.id,
            record=record,
            hits=(HitSignal(lane=RecallLane.SESSION_CURRENT, similarity=0.9),),
        )
        assert EligibleCandidate.model_validate(candidate.model_dump()) == candidate

    def test_scored_candidate_roundtrip(self):
        record = _record()
        scored = ScoredCandidate(
            memory_id=record.id,
            record=record,
            components=(
                ScoreComponent(name="semantic", value=0.8, explanation="close"),
            ),
            utility=0.75,
            confidence=0.6,
            provisional_role=EvidenceRole.CURRENT,
        )
        assert ScoredCandidate.model_validate(scored.model_dump()) == scored

    def test_evidence_group_roundtrip(self):
        record = _record()
        item = EvidenceItem(
            evidence_id="e1",
            memory_id=record.id,
            role=EvidenceRole.CURRENT,
            content=record.content,
            memory_type=record.type,
            scope=record.scope,
        )
        group = EvidenceGroup(group_id="g1", primary=item)
        assert EvidenceGroup.model_validate(group.model_dump()) == group


class TestStageContractImmutability:
    def test_intent_is_frozen(self):
        intent = RecallIntent(primary_query="q")
        with pytest.raises(ValidationError):
            intent.primary_query = "other"  # type: ignore[misc]

    def test_eligible_candidate_is_frozen(self):
        candidate = EligibleCandidate(memory_id="m1", record=_record())
        with pytest.raises(ValidationError):
            candidate.temporal_role = TemporalIntent.CURRENT_STATE  # type: ignore[misc]

    def test_scored_candidate_is_frozen(self):
        scored = ScoredCandidate(memory_id="m1", record=_record())
        with pytest.raises(ValidationError):
            scored.utility = 0.5  # type: ignore[misc]

    def test_evidence_group_is_frozen(self):
        group = EvidenceGroup(
            group_id="g1",
            primary=EvidenceItem(
                evidence_id="e1",
                memory_id="m1",
                role=EvidenceRole.CURRENT,
                content="x",
                memory_type=MemoryType.FACT,
                scope=MemoryScope(user_id="u1"),
            ),
        )
        with pytest.raises(ValidationError):
            group.resolution = "r"  # type: ignore[misc]


class TestRecallFieldsDoNotMutateMemoryRecord:
    """Temporary Recall fields live on stage wrappers, never on MemoryRecord."""

    def test_memory_record_schema_excludes_recall_fields(self):
        recall_fields = {
            "utility",
            "provisional_role",
            "components",
            "hits",
            "temporal_role",
            "scoring_fallback",
            "confidence_score",
        }
        assert recall_fields.isdisjoint(MemoryRecord.model_fields.keys())

    def test_scoring_keeps_record_unchanged(self):
        record = _record()
        original_dump = record.model_dump()
        scored = ScoredCandidate(
            memory_id=record.id,
            record=record,
            utility=0.9,
            components=(ScoreComponent(name="semantic", value=0.8),),
            provisional_role=EvidenceRole.CURRENT,
            scoring_fallback=True,
        )
        assert record.model_dump() == original_dump
        assert scored.utility == 0.9
        assert scored.record == record

    def test_eligibility_keeps_record_unchanged(self):
        record = _record()
        original_dump = record.model_dump()
        EligibleCandidate(
            memory_id=record.id,
            record=record,
            hits=(HitSignal(lane=RecallLane.SESSION_CURRENT),),
            temporal_role=TemporalIntent.CURRENT_STATE,
        )
        assert record.model_dump() == original_dump

    def test_rejected_candidate_carries_reason_without_record(self):
        rejected = RejectedCandidate(memory_id="m1", reason=RejectionReason.WRONG_USER)
        assert rejected.reason is RejectionReason.WRONG_USER
        assert "record" not in RejectedCandidate.model_fields
