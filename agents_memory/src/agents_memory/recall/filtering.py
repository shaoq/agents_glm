"""Deterministic Recall eligibility filtering.

Hard filters run before utility scoring and can never be overridden by a high
score or an LLM judgment. The filter hydrates candidates from SQLite (the truth
source), merges multi-path hits per memory, and drops anything failing user
boundary, integrity, validity, explicit type or hard-time constraints.

Reference: design 7 (eligibility filtering) and 6.5 (SQLite rehydration).
"""

from collections import defaultdict

from agents_memory.models import Validity
from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import (
    EligibleCandidate,
    RecallRequest,
    RejectionReason,
    RetrievedCandidate,
    TemporalIntent,
)
from agents_memory.storage.recalls import RecallReadRepository

_HISTORICAL_INTENTS = (
    TemporalIntent.POINT_IN_TIME,
    TemporalIntent.INTERVAL,
    TemporalIntent.EVOLUTION,
)


def assess_eligibility(request: RecallRequest, record) -> RejectionReason | None:
    """Pure hard-filter check. Returns the first failing reason or None.

    Order matters (design 7.1): integrity, user boundary, validity, explicit
    type, hard time. A hit on any hard filter stops processing.
    """

    if not record.content:
        return RejectionReason.CORRUPT_RECORD
    if record.scope.user_id != request.user_id:
        return RejectionReason.WRONG_USER
    if not _validity_ok(request, record):
        return RejectionReason.INVALID_STATE
    if request.explicit_types and record.type not in request.explicit_types:
        return RejectionReason.EXPLICIT_TYPE_VIOLATION
    if request.explicit_time_range and not _time_ok(request, record):
        return RejectionReason.HARD_TIME_VIOLATION
    return None


def _validity_ok(request: RecallRequest, record) -> bool:
    if record.validity is Validity.ACTIVE:
        return True
    if record.validity is Validity.SUPERSEDED:
        return request.temporal_intent in _HISTORICAL_INTENTS
    return False  # RETRACTED is never fact evidence in the first release


def _time_ok(request: RecallRequest, record) -> bool:
    anchor = request.explicit_time_range
    if anchor is None or anchor.start is None or anchor.end is None:
        return True
    point = record.valid_from or record.created_at
    if point is None:
        return True
    return anchor.start <= point <= anchor.end


class EligibilityFilter:
    """Hydrates candidates from SQLite and applies hard eligibility filters."""

    def __init__(self, repository: RecallReadRepository) -> None:
        self.repository = repository

    def filter(
        self,
        request: RecallRequest,
        candidates: tuple[RetrievedCandidate, ...],
        diag: RecallDiagnostics,
    ) -> tuple[EligibleCandidate, ...]:
        if not candidates:
            return ()
        unique_ids = tuple({candidate.memory_id for candidate in candidates})
        records = {
            record.id: record
            for record in self.repository.load_memories_by_ids(
                unique_ids, user_id=request.user_id, limit=len(unique_ids)
            )
        }
        hits_by_id: dict[str, list] = defaultdict(list)
        for candidate in candidates:
            hits_by_id[candidate.memory_id].extend(candidate.hits)
        eligible: list[EligibleCandidate] = []
        for memory_id, hits in hits_by_id.items():
            record = records.get(memory_id)
            if record is None:
                self._note_rejection(diag, request, memory_id, RejectionReason.RECORD_MISSING)
                continue
            reason = assess_eligibility(request, record)
            if reason is not None:
                self._note_rejection(diag, request, memory_id, reason)
                continue
            eligible.append(EligibleCandidate(memory_id=memory_id, record=record, hits=tuple(hits)))
        return tuple(eligible)

    @staticmethod
    def _note_rejection(
        diag: RecallDiagnostics,
        request: RecallRequest,
        memory_id: str,
        reason: RejectionReason,
    ) -> None:
        if request.diagnostic:
            diag.note(f"rejected {memory_id}: {reason.value}")
