"""JSON mappers for the hybrid schema (key columns + ``data`` blob)."""

from __future__ import annotations

from pydantic import BaseModel


def dump(model: BaseModel) -> str:
    """Serialize a Pydantic model to canonical JSON."""

    return model.model_dump_json()


def load[T: BaseModel](cls: type[T], text: str) -> T:
    """Deserialize JSON into a Pydantic model."""

    return cls.model_validate_json(text)
