"""Optional, explicitly-enabled live adapter smoke tests (task 13.7).

These are SKIPPED by default so the default suite makes no real network calls
(task 13.8). Enable by setting ``ORCH_LIVE_SMOKE=1`` and providing real credentials
in ``.env``. They exercise the real Memory/RAG/Web/Model adapters against live
services and are not part of CI coverage.
"""

from __future__ import annotations

import os

import pytest

_LIVE = os.environ.get("ORCH_LIVE_SMOKE") == "1"
_SKIP = pytest.mark.skipif(not _LIVE, reason="live smoke tests require ORCH_LIVE_SMOKE=1")


@_SKIP
@pytest.mark.smoke
async def test_live_model_adapter() -> None:
    from agents_orchestration.adapters.base import ModelProfile
    from agents_orchestration.adapters.model import OpenAIModelAdapter
    from agents_orchestration.config import load_settings
    from agents_orchestration.domain.capability import CapabilityRequest

    settings = load_settings()
    profile = ModelProfile(
        name=settings.model_planner,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    adapter = OpenAIModelAdapter(profile)
    result = await adapter.invoke(
        CapabilityRequest(
            request_id="smoke",
            capability_id="model",
            worker_id="w",
            run_id="r1",
            task_id="t1",
            attempt_id="a1",
            inputs={"prompt": "Say hello in one word."},
        )
    )
    assert result.succeeded


@_SKIP
@pytest.mark.smoke
async def test_live_memory_and_rag_adapters() -> None:
    # Constructed against live MemoryService / QueryPipeline at the composition root.
    pytest.skip("wire live Memory/RAG services at the composition root to run")
