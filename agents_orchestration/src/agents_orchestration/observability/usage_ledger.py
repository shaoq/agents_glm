"""Token / cost / latency / retry / degradation Usage Ledger (task 12.8).

Aggregates per-capability usage so the run summary and diagnostics can disclose
real resource consumption and degradation rather than faking success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from agents_orchestration.domain.evidence import Usage


@dataclass(frozen=True)
class UsageEntry:
    run_id: str
    attempt_id: str
    capability_id: str
    usage: Usage
    degraded: bool = False
    retry: bool = False


@dataclass
class UsageLedger:
    entries: list[UsageEntry] = field(default_factory=list)

    def record(
        self,
        *,
        run_id: str,
        attempt_id: str,
        capability_id: str,
        usage: Usage,
        degraded: bool = False,
        retry: bool = False,
    ) -> UsageEntry:
        entry = UsageEntry(run_id, attempt_id, capability_id, usage, degraded, retry)
        self.entries.append(entry)
        return entry

    def total(self, run_id: str) -> Usage:
        tokens = sum(e.usage.tokens for e in self.entries if e.run_id == run_id)
        cost = sum((e.usage.cost_usd for e in self.entries if e.run_id == run_id), Decimal("0"))
        latency = sum(e.usage.latency_ms for e in self.entries if e.run_id == run_id)
        retries = sum(e.usage.retries for e in self.entries if e.run_id == run_id)
        return Usage(tokens=tokens, cost_usd=cost, latency_ms=latency, retries=retries)

    def to_records(self, run_id: str) -> list[dict]:
        return [
            {
                "attempt_id": e.attempt_id,
                "capability_id": e.capability_id,
                "tokens": e.usage.tokens,
                "cost_usd": str(e.usage.cost_usd),
                "latency_ms": e.usage.latency_ms,
                "degraded": e.degraded,
                "retry": e.retry,
            }
            for e in self.entries
            if e.run_id == run_id
        ]
