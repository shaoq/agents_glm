"""Pure event-frame matching shared by write-time reconciliation paths."""

from agents_memory.models import CandidateMemory, EventFrame


def frames_related(left: EventFrame | None, right: EventFrame | None) -> bool:
    """Return whether all comparable event dimensions agree.

    Actor alone is intentionally insufficient: recurring actions by the same
    actor must not be collapsed without a comparable predicate, object, or
    location.
    """

    if left is None or right is None:
        return False
    comparable = [
        (getattr(left, field), getattr(right, field))
        for field in ("predicate", "object", "location")
        if getattr(left, field) != "unknown" and getattr(right, field) != "unknown"
    ]
    return bool(comparable) and all(a == b for a, b in comparable)


def group_frames_related(
    grouped: tuple[CandidateMemory, ...],
    candidate: CandidateMemory,
) -> bool:
    """Require a candidate to agree with every assertion already in a group."""

    return bool(grouped) and all(
        frames_related(item.event_frame, candidate.event_frame) for item in grouped
    )
