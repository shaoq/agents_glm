"""agents_orchestration: local-first durable orchestration runtime.

Layered as::

    CLI → Application → Domain + Runtime Ports ← Infrastructure Adapters

Subpackages and their boundaries:

- :mod:`agents_orchestration.domain` — pure domain models, typed enums, state
  machines and domain events. Imports NO infrastructure (no sqlite3, typer, rich,
  openai, httpx, agents_memory, agents_rag).
- :mod:`agents_orchestration.runtime` — durable runtime core (scheduler, lease,
  budget, retry, checkpoint, recovery, tick, watch) plus the Port protocols
  (Repository, Transaction, EventStore, Outbox, ArtifactStore, Clock, IDGenerator)
  and the SQLite persistence adapters that implement them.
- :mod:`agents_orchestration.orchestration` — control plane: goal normalization,
  dynamic planning, plan validation, gate management and termination guarding.
- :mod:`agents_orchestration.workers` — worker definitions, registry and executor.
- :mod:`agents_orchestration.capabilities` — capability descriptors, registry and
  router.
- :mod:`agents_orchestration.adapters` — research capability adapters (Fake,
  Memory, RAG, Web, Model). This is the ONLY package allowed to import
  ``agents_memory``, ``agents_rag`` or provider SDKs.
- :mod:`agents_orchestration.application` — use-case OrchestrationService that
  composes the layers above; the CLI is a thin adapter over it.
"""

__version__ = "0.1.0"
