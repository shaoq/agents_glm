from datetime import UTC, datetime, timedelta

from agents_memory.models import CandidateMemory, TemporalResolution


class PendingResolutionPolicy:
    def __init__(
        self,
        *,
        high_days: int = 30,
        normal_days: int = 7,
        low_days: int = 1,
    ) -> None:
        self.high_days = high_days
        self.normal_days = normal_days
        self.low_days = low_days

    def expires_at(
        self, importance: int, *, now: datetime | None = None
    ) -> datetime:
        current = now or datetime.now(UTC)
        days = (
            self.high_days
            if importance >= 8
            else self.normal_days
            if importance >= 5
            else self.low_days
        )
        return current + timedelta(days=days)


def missing_event_dimensions(candidate: CandidateMemory) -> tuple[str, ...]:
    frame = candidate.event_frame
    if frame is None:
        return ("event_frame",)
    missing = [
        name
        for name in ("actor", "predicate", "object", "location")
        if getattr(frame, name) == "unknown"
    ]
    if frame.temporal_anchor.resolution is TemporalResolution.UNRESOLVED:
        missing.append("event_time")
    return tuple(missing)
