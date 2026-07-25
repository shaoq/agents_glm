"""Set-level evidence selection.

Selects EvidenceGroups (not raw records) under the evidence-count budget using
a explainable marginal-value ordering: direct/primary value plus a bonus for
relation-rich groups (history, conflict sides, supporting context). Conflict
groups are atomic and never split to fit the budget.

Reference: design 10 (set selection).
"""

from agents_memory.recall.diagnostics import RecallDiagnostics
from agents_memory.recall.models import EvidenceGroup, RecallRequest


def group_value(group: EvidenceGroup) -> float:
    """Explainable per-group value: primary importance + relation richness."""

    base = group.primary.importance / 10.0
    members = 1 + len(group.historical) + len(group.conflicting) + len(group.supporting)
    return base + 0.1 * (members - 1)


class SetSelector:
    """Greedy marginal-value selector bounded by max_evidence_items."""

    def select(
        self,
        request: RecallRequest,
        groups: tuple[EvidenceGroup, ...],
        diag: RecallDiagnostics,  # noqa: ARG002 (deterministic; protocol symmetry)
    ) -> tuple[EvidenceGroup, ...]:
        if not groups:
            return ()
        ranked = sorted(groups, key=group_value, reverse=True)
        return tuple(ranked[: request.max_evidence_items])
