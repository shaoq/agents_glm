import pytest

from agents_memory.models import (
    Action,
    CandidateMemory,
    EventIdentity,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    RelationKind,
    RelationMatch,
    SourceKind,
    TemporalRelation,
    Validity,
)
from agents_memory.processing.decision import AmbiguousDecision, DecisionEngine


def candidate(type: MemoryType = MemoryType.FACT) -> CandidateMemory:
    return CandidateMemory(
        content="new",
        type=type,
        importance=7,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
    )


def record(
    memory_id: str,
    *,
    type: MemoryType = MemoryType.FACT,
    validity: Validity = Validity.ACTIVE,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(user_id="u1"),
        type=type,
        content="old",
        importance=7,
        confidence=0.9,
        validity=validity,
    )


@pytest.mark.parametrize(
    ("relations", "expected"),
    [
        ([], Action.ADD),
        ([RelationMatch(memory_id="old", relation=RelationKind.SUPPLEMENT)], Action.ADD),
        ([RelationMatch(memory_id="old", relation=RelationKind.DUPLICATE)], Action.NOOP),
        ([RelationMatch(memory_id="old", relation=RelationKind.CONTRADICT)], Action.UPDATE),
        ([RelationMatch(memory_id="old", relation=RelationKind.CORRECT)], Action.UPDATE),
    ],
)
def test_decision_matrix(relations: list[RelationMatch], expected: Action) -> None:
    histories = [record("old")] if relations else []
    plan = DecisionEngine().decide(0, candidate(), histories, relations)
    assert plan.action is expected


def test_unknown_event_contradiction_is_deferred() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.CONTRADICT,
                identity=EventIdentity.UNKNOWN,
                temporal=TemporalRelation.UNKNOWN,
            )
        ],
    )
    assert plan.action is Action.DEFER
    assert plan.target_ids == ("old",)


def test_unknown_event_defer_preserves_all_related_targets() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [
            record("same", type=MemoryType.EVENT),
            record("unknown", type=MemoryType.EVENT),
        ],
        [
            RelationMatch(
                memory_id="same",
                relation=RelationKind.CONTRADICT,
                identity=EventIdentity.SAME_EVENT,
                temporal=TemporalRelation.SAME_WINDOW,
            ),
            RelationMatch(
                memory_id="unknown",
                relation=RelationKind.CONTRADICT,
                identity=EventIdentity.UNKNOWN,
                temporal=TemporalRelation.UNKNOWN,
            ),
        ],
    )

    assert plan.action is Action.DEFER
    assert plan.target_ids == ("same", "unknown")


def test_explicit_event_correction_updates() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.CORRECT,
                identity=EventIdentity.SAME_EVENT,
                temporal=TemporalRelation.SAME_WINDOW,
                explicit_correction=True,
            )
        ],
    )
    assert plan.action is Action.UPDATE


def test_different_event_contradiction_adds_without_replacing_history() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.CONTRADICT,
                identity=EventIdentity.DIFFERENT_EVENT,
                temporal=TemporalRelation.AFTER,
            )
        ],
    )

    assert plan.action is Action.ADD
    assert plan.target_ids == ()


def test_different_event_explicit_correction_is_deferred_as_inconsistent() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.CORRECT,
                identity=EventIdentity.DIFFERENT_EVENT,
                temporal=TemporalRelation.AFTER,
                explicit_correction=True,
            )
        ],
    )

    assert plan.action is Action.DEFER
    assert plan.target_ids == ("old",)


def test_same_event_state_change_updates() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.CONTRADICT,
                identity=EventIdentity.SAME_EVENT,
                temporal=TemporalRelation.SAME_WINDOW,
            )
        ],
    )

    assert plan.action is Action.UPDATE
    assert plan.target_ids == ("old",)


def test_same_event_duplicate_noops() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(MemoryType.EVENT),
        [record("old", type=MemoryType.EVENT)],
        [
            RelationMatch(
                memory_id="old",
                relation=RelationKind.DUPLICATE,
                identity=EventIdentity.SAME_EVENT,
                temporal=TemporalRelation.SAME_WINDOW,
            )
        ],
    )

    assert plan.action is Action.NOOP


def test_duplicate_of_history_does_not_noop() -> None:
    plan = DecisionEngine().decide(
        0,
        candidate(),
        [record("old", validity=Validity.SUPERSEDED)],
        [RelationMatch(memory_id="old", relation=RelationKind.DUPLICATE)],
    )
    assert plan.action is Action.ADD


def test_mixed_supplement_and_contradict_is_ambiguous() -> None:
    with pytest.raises(AmbiguousDecision):
        DecisionEngine().decide(
            0,
            candidate(),
            [record("a"), record("b")],
            [
                RelationMatch(memory_id="a", relation=RelationKind.SUPPLEMENT),
                RelationMatch(memory_id="b", relation=RelationKind.CONTRADICT),
            ],
        )
