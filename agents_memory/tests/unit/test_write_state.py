from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    SourceKind,
    Validity,
)
from agents_memory.pipeline.state import WriteBatchState


def candidate(content: str, type_: MemoryType = MemoryType.FACT) -> CandidateMemory:
    return CandidateMemory(
        content=content,
        type=type_,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def record(id_: str, content: str, type_: MemoryType = MemoryType.FACT) -> MemoryRecord:
    return MemoryRecord(
        id=id_,
        scope=MemoryScope(user_id="u1"),
        type=type_,
        content=content,
        importance=8,
        confidence=0.9,
        validity=Validity.ACTIVE,
    )


def test_state_records_plans_embeddings_and_staged_add() -> None:
    state = WriteBatchState()
    plan = ActionPlan(
        candidate_index=0,
        candidate=candidate("用户偏好 Python"),
        action=Action.ADD,
    )

    staged_plan = state.stage_materialized(
        plan,
        scope=MemoryScope(user_id="u1"),
        memory_id="new",
    )
    state.record(staged_plan, embedding=[1.0, 0.0])

    assert staged_plan.new_memory_id == "new"
    assert state.plans == [staged_plan]
    assert state.embeddings == {0: [1.0, 0.0]}
    assert [item.id for item in state.staged_memories] == ["new"]


def test_update_hides_target_and_replaces_staged_memory() -> None:
    state = WriteBatchState()
    added = state.stage_materialized(
        ActionPlan(
            candidate_index=0,
            candidate=candidate("old staged"),
            action=Action.ADD,
        ),
        scope=MemoryScope(user_id="u1"),
        memory_id="staged-old",
    )
    state.record(added, embedding=[1.0, 0.0])

    updated = state.stage_materialized(
        ActionPlan(
            candidate_index=1,
            candidate=candidate("new staged"),
            action=Action.UPDATE,
            target_ids=("stored-old", "staged-old"),
        ),
        scope=MemoryScope(user_id="u1"),
        memory_id="staged-new",
    )

    assert state.inactive_ids == {"stored-old", "staged-old"}
    assert [item.id for item in state.staged_memories] == ["staged-new"]
    assert updated.new_memory_id == "staged-new"


def test_visible_histories_apply_overlay_and_memory_type_boundary() -> None:
    state = WriteBatchState()
    state.inactive_ids.add("inactive")
    state.staged_memories.extend(
        [
            record("staged-fact", "fact"),
            record("staged-event", "event", MemoryType.EVENT),
        ]
    )

    histories = state.visible_histories(
        [record("active", "active"), record("inactive", "inactive")],
        MemoryType.FACT,
    )

    assert [item.id for item in histories] == ["active", "staged-fact"]
