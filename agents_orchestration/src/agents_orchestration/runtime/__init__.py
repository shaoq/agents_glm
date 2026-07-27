"""Durable runtime layer.

Contains the deterministic runtime core (scheduler, lease manager, budget guard,
retry, checkpoint, recovery, tick, watch), the Port protocols
(:mod:`agents_orchestration.runtime.ports`), and the SQLite persistence adapters
(:mod:`agents_orchestration.runtime.persistence`) that implement those ports.

The runtime core depends only on :mod:`agents_orchestration.domain` and its own
Port protocols; it never calls a provider SDK directly.
"""
