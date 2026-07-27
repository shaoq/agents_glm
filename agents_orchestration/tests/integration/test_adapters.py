"""Integration tests for research capability adapters (Section 7)."""

from __future__ import annotations

import asyncio
import time

import pytest

from agents_orchestration.adapters.base import ModelProfile, select_model_profile, to_async
from agents_orchestration.adapters.fake import build_fake_registry
from agents_orchestration.adapters.health import capability_doctor
from agents_orchestration.adapters.memory import MemoryRecallAdapter
from agents_orchestration.adapters.rag import RagAdapter
from agents_orchestration.adapters.web import WebResearchAdapter
from agents_orchestration.capabilities.router import CapabilityRouter
from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.enums import CapabilityKind, WorkerRole
from agents_orchestration.domain.evidence import Evidence, SourceIdentity, SourceKind
from agents_orchestration.domain.policy import RunPolicy, SystemLimits
from agents_orchestration.workers.registry import WorkerRegistry


def _req(cap_id: str, **inputs) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="req",
        capability_id=cap_id,
        worker_id="w",
        run_id="r1",
        task_id="t1",
        attempt_id="a1",
        inputs=dict(inputs),
    )


def _policy(**overrides) -> RunPolicy:
    return RunPolicy.from_limits(SystemLimits(), **overrides)


# --- 7.1 Fake adapters via the router ---------------------------------------


@pytest.mark.integration
async def test_fake_registry_round_trip_memory_rag_model(backend) -> None:
    registry = build_fake_registry()
    router = CapabilityRouter(registry, backend.idgen)
    workers = WorkerRegistry.default()
    researcher = workers.get(WorkerRole.EVIDENCE_RESEARCHER)
    analyst = workers.get(WorkerRole.ANALYST)

    rag = await router.invoke(_req("fake::rag"), worker=researcher, run_policy=_policy())
    memory = await router.invoke(_req("fake::memory"), worker=researcher, run_policy=_policy())
    model = await router.invoke(_req("fake::model"), worker=analyst, run_policy=_policy())

    assert rag.succeeded and rag.evidence[0].source.source_kind is SourceKind.RAG
    assert memory.succeeded and memory.evidence[0].source.source_kind is SourceKind.MEMORY
    assert model.succeeded and model.data["text"]


@pytest.mark.integration
async def test_fake_web_denied_when_disabled(backend) -> None:
    registry = build_fake_registry(web_enabled=False)
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    result = await router.invoke(
        _req("fake::web", url="https://example.com/x"),
        worker=researcher,
        run_policy=_policy(),
    )
    assert not result.succeeded


@pytest.mark.integration
async def test_fake_web_allowed_when_enabled_and_domain_permitted(backend) -> None:
    registry = build_fake_registry(web_enabled=True)
    router = CapabilityRouter(registry, backend.idgen)
    researcher = WorkerRegistry.default().get(WorkerRole.EVIDENCE_RESEARCHER)
    result = await router.invoke(
        _req("fake::web", url="https://example.com/x"),
        worker=researcher,
        run_policy=_policy(web_enabled=True, web_allowed_domains=("example.com",)),
    )
    assert result.succeeded


# --- 7.2 / 7.4 injected-boundary adapters degrade on empty / error -----------


@pytest.mark.integration
async def test_memory_adapter_degrades_on_empty() -> None:
    adapter = MemoryRecallAdapter(recall_fn=lambda query, scope: ())
    result = await adapter.invoke(_req("memory"))
    assert result.is_degraded and result.evidence == ()


@pytest.mark.integration
async def test_rag_adapter_degrades_on_error() -> None:
    def boom(_query):
        raise RuntimeError("rag down")

    adapter = RagAdapter(query_fn=boom)
    result = await adapter.invoke(_req("rag"))
    assert result.is_degraded


@pytest.mark.integration
async def test_web_adapter_with_injected_fetch() -> None:
    def fetch(url):
        return (
            Evidence(
                evidence_id="w1",
                source=SourceIdentity(source_id=url, source_kind=SourceKind.WEB, uri=url),
                content_text="page",
            ),
        )

    adapter = WebResearchAdapter(fetch_fn=fetch)
    result = await adapter.invoke(_req("web", url="https://x.example.com/"))
    assert result.succeeded and result.evidence[0].source.source_kind is SourceKind.WEB


# --- 7.8 async bridge concurrency ------------------------------------------


@pytest.mark.integration
async def test_to_async_runs_bounded_and_concurrent() -> None:
    def slow(t: float) -> float:
        time.sleep(t)
        return t

    start = time.perf_counter()
    results = await asyncio.gather(to_async(slow, 0.15), to_async(slow, 0.15))
    elapsed = time.perf_counter() - start
    assert results == [0.15, 0.15]
    # Two 0.15s calls must overlap (serial would be ~0.30s).
    assert elapsed < 0.28


# --- 7.9 capability doctor --------------------------------------------------


@pytest.mark.integration
def test_capability_doctor_reports_all_without_secrets() -> None:
    registry = build_fake_registry()
    report = capability_doctor(registry)
    assert len(report) == 4
    kinds = {entry["kind"] for entry in report}
    assert kinds == {k.value for k in CapabilityKind}
    for entry in report:
        assert "api_key" not in entry
        assert entry["permission"] == "read"


# --- 6.7 model profile routing ----------------------------------------------


@pytest.mark.integration
def test_select_model_profile_routes_by_role() -> None:
    normalizer = ModelProfile(name="n", base_url="u", api_key="k1")
    planner = ModelProfile(name="p", base_url="u", api_key="k2")
    reviewer = ModelProfile(name="rv", base_url="u", api_key="k3")
    assert (
        select_model_profile(
            WorkerRole.RESEARCH_PLANNER, normalizer=normalizer, planner=planner, reviewer=reviewer
        )
        is planner
    )
    assert (
        select_model_profile(
            WorkerRole.REPORT_REVIEWER, normalizer=normalizer, planner=planner, reviewer=reviewer
        )
        is reviewer
    )
    assert (
        select_model_profile(
            WorkerRole.ANALYST, normalizer=normalizer, planner=planner, reviewer=reviewer
        )
        is normalizer
    )
