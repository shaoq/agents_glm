"""Immutable identifiers for the four identity layers (design Decision 3).

Run → Task → Attempt → Operation. Plus identifiers for Plan/State versions,
Lease epochs, Gates, Events, Checkpoints and Artifacts.

IDs are opaque strings (ULID-like). Concrete generation lives behind the
``IDGenerator`` Port (task 3.1); the domain only defines the type aliases and a
default factory so models can be constructed in tests without a live generator.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import StringConstraints

# Each identifier is a constrained non-empty string. They share a storage type
# (``str``) but carry distinct names so call sites and schemas read clearly.
RunId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
TaskId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
AttemptId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
OperationId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PlanId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
GateId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
EventId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
CheckpointId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ArtifactId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
CapabilityId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
WorkerId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
BranchId = Annotated[str, StringConstraints(min_length=1, max_length=64)]

# Monotonic counters expressed as ints.
StateVersion = int
PlanVersion = int
LeaseEpoch = int


def new_id(prefix: str) -> str:
    """Default id factory: ``<prefix>_<uuid4_hex>``.

    Used only as a model default and in tests; production code injects an
    ``IDGenerator`` Port so ids are deterministic / ULID-ordered when required.
    """

    return f"{prefix}_{uuid.uuid4().hex}"
