"""Unit tests for function-calling Model adapter + tool-schema helper (Ch.1 tasks 1.3/1.4)."""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import BaseModel

from agents_orchestration.adapters.base import ModelProfile
from agents_orchestration.adapters.llm_tools import pydantic_to_tool
from agents_orchestration.adapters.model import OpenAIModelAdapter
from agents_orchestration.domain.capability import CapabilityRequest
from agents_orchestration.domain.enums import FailureCode


def _req(**inputs: object) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="r",
        capability_id="model::openai",
        worker_id="w",
        run_id="run",
        task_id="t",
        attempt_id="a",
        inputs=dict(inputs),
    )


def _msg(tool_calls=None, content=""):
    return types.SimpleNamespace(tool_calls=tool_calls, content=content)


def _tool_call(name, args):
    return types.SimpleNamespace(function=types.SimpleNamespace(name=name, arguments=args))


def _response(msg, tokens=10):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)],
        usage=types.SimpleNamespace(total_tokens=tokens),
    )


def _install_openai(monkeypatch, response, exc=None, capture=None) -> None:
    """Install a fake ``openai`` module in sys.modules whose create() returns response."""

    class _Completions:
        def create(self, **kw):
            if exc is not None:
                raise exc
            return response

    class _Chat:
        completions = _Completions()

    def _openai(**kw):
        if capture is not None:
            capture.update(kw)
        return types.SimpleNamespace(chat=_Chat)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_openai))


class _Out(BaseModel):
    objective: str
    deliverables: list[str] = []


# --- pydantic_to_tool (1.3) ---


@pytest.mark.unit
def test_pydantic_to_tool_generates_schema() -> None:
    tool = pydantic_to_tool(_Out, name="submit_goal", description="submit a goal")
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "submit_goal"
    assert fn["description"] == "submit a goal"
    assert fn["parameters"]["type"] == "object"
    assert "objective" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["objective"]


# --- invoke_tools (1.1 / 1.2 / 1.4) ---


@pytest.mark.unit
async def test_invoke_tools_parses_tool_call(monkeypatch) -> None:
    resp = _response(_msg(tool_calls=[_tool_call("submit_goal", '{"objective":"X"}')]))
    _install_openai(monkeypatch, resp)
    adapter = OpenAIModelAdapter(ModelProfile(name="glm-5.2", base_url="u", api_key="k"))
    result = await adapter.invoke_tools(
        _req(prompt="p"), [pydantic_to_tool(_Out, name="submit_goal", description="d")]
    )
    assert result.succeeded
    assert result.data["tool_name"] == "submit_goal"
    assert result.data["arguments"] == '{"objective":"X"}'
    assert result.usage.tokens == 10


@pytest.mark.unit
async def test_invoke_tools_no_tool_call_is_invalid(monkeypatch) -> None:
    _install_openai(monkeypatch, _response(_msg(tool_calls=None, content="hi")))
    adapter = OpenAIModelAdapter(ModelProfile(name="m", base_url="u", api_key="k"))
    result = await adapter.invoke_tools(_req(prompt="p"), [])
    assert not result.succeeded
    assert result.failure_code is FailureCode.INVALID_RESPONSE


@pytest.mark.unit
async def test_invoke_tools_provider_error_is_upstream(monkeypatch) -> None:
    _install_openai(monkeypatch, None, exc=RuntimeError("boom"))
    adapter = OpenAIModelAdapter(ModelProfile(name="m", base_url="u", api_key="k"))
    result = await adapter.invoke_tools(_req(prompt="p"), [])
    assert not result.succeeded
    assert result.failure_code is FailureCode.UPSTREAM_ERROR
    assert result.retryable


@pytest.mark.unit
async def test_secret_passed_at_boundary_not_in_result(monkeypatch) -> None:
    capture: dict = {}
    resp = _response(_msg(tool_calls=[_tool_call("t", "{}")]))
    _install_openai(monkeypatch, resp, capture=capture)
    adapter = OpenAIModelAdapter(ModelProfile(name="m", base_url="u", api_key="sk-SECRET"))
    result = await adapter.invoke_tools(_req(prompt="p"), [])
    assert capture.get("api_key") == "sk-SECRET"  # read at the boundary
    blob = repr(result.data) + str(result.data.get("arguments", ""))
    assert "sk-SECRET" not in blob
