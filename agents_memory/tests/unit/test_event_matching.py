from agents_memory.models import CandidateMemory, EventFrame, MemoryType, SourceKind
from agents_memory.processing.event_matching import (
    frames_related,
    group_frames_related,
)


def event_candidate(*, predicate: str, object_: str) -> CandidateMemory:
    return CandidateMemory(
        content=f"{predicate}:{object_}",
        type=MemoryType.EVENT,
        importance=8,
        confidence=0.9,
        source_message_ids=("m1",),
        source_kind=SourceKind.USER_EXPLICIT,
        event_frame=EventFrame(
            actor="user",
            predicate=predicate,
            object=object_,
        ),
    )


def test_frames_without_comparable_dimensions_are_unrelated() -> None:
    assert not frames_related(None, None)
    assert not frames_related(EventFrame(actor="user"), EventFrame(actor="user"))


def test_frames_require_all_known_dimensions_to_match() -> None:
    left = EventFrame(predicate="travel", object="北京", location="北京")

    assert frames_related(
        left,
        EventFrame(predicate="travel", object="北京"),
    )
    assert not frames_related(
        left,
        EventFrame(predicate="travel", object="上海"),
    )


def test_group_matching_requires_every_member_to_be_related() -> None:
    sparse = event_candidate(predicate="travel", object_="unknown")
    beijing = event_candidate(predicate="travel", object_="北京")
    shanghai = event_candidate(predicate="travel", object_="上海")

    assert group_frames_related((beijing,), beijing)
    assert not group_frames_related((sparse, beijing), shanghai)
    assert not group_frames_related((), beijing)
