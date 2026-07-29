"""Shared adapter base, model profile and async bridge helpers (tasks 6.7 / 7.8).

Real adapters (Memory/RAG/Web/Model) import siblings / provider SDKs lazily inside
``invoke`` so importing this package never requires those dependencies.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

from agents_orchestration.domain.capability import (
    CapabilityDescriptor,
    CapabilityRequest,
    CapabilityResult,
    HealthState,
)
from agents_orchestration.domain.enums import CapabilityKind, WorkerRole

# A single bounded executor backs the async bridge (task 7.8): synchronous
# Memory/RAG calls are offloaded so unrelated Tasks stay concurrent.
_BRIDGE = ThreadPoolExecutor(max_workers=8, thread_name_prefix="orch-bridge")


async def to_async(func, /, *args, **kwargs):
    """Run a synchronous callable in the bounded bridge executor."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_BRIDGE, partial(func, *args, **kwargs))


@dataclass(frozen=True)
class ModelProfile:
    """A named, OpenAI-compatible model profile (task 6.7 / 7.7)."""

    name: str
    base_url: str
    api_key: str
    timeout_seconds: float = 30.0


def select_model_profile(
    role: WorkerRole, *, normalizer: ModelProfile, planner: ModelProfile, reviewer: ModelProfile
) -> ModelProfile:
    """Route each model-backed component to its profile (task 6.7).

    GoalNormalizer and the ResearchPlanner share the planner profile in the first
    release; the ReportReviewer uses the reviewer profile; other model-backed
    workers default to the planner profile.
    """

    if role is WorkerRole.RESEARCH_PLANNER:
        return planner
    if role is WorkerRole.REPORT_REVIEWER:
        return reviewer
    return normalizer


class AsyncCapabilityAdapter:
    """Convenience base: holds the descriptor and a default health probe."""

    def __init__(self, descriptor: CapabilityDescriptor) -> None:
        self.descriptor = descriptor

    def health(self) -> HealthState:
        return self.descriptor.health

    async def invoke(self, request: CapabilityRequest) -> CapabilityResult:  # pragma: no cover
        raise NotImplementedError


def descriptor_for(kind: CapabilityKind, capability_id: str | None = None) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id or f"cap::{kind.value}", kind=kind.value
    )
