"""Derive OpenAI tool definitions from Pydantic models (Ch.1 task 1.3).

Each LLM phase port builds its tool definition from the output domain model's
JSON schema, so the model is constrained to emit ``tool_call.function.arguments``
that parse cleanly into the typed model.
"""

from __future__ import annotations

from pydantic import BaseModel


def pydantic_to_tool(
    model_cls: type[BaseModel], *, name: str, description: str
) -> dict:
    """Build a function-calling tool definition from a Pydantic model's JSON schema."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model_cls.model_json_schema(),
        },
    }


__all__ = ["pydantic_to_tool"]
