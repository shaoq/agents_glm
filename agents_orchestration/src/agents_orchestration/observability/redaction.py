"""Secret redaction and safe event projection (tasks 12.4 / 12.9).

Secrets are read only at Adapter boundaries from Settings; everything that leaves
a boundary — logs, events streamed to ``run watch``, diagnostics — is redacted so
a key can never leak into state, prompts, events, checkpoints, artifacts or logs.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "authorization",
    "credential",
)
_SECRET_VALUE_RE = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE)


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in _SECRET_KEY_PATTERNS)


def redact(obj: Any) -> Any:
    """Recursively replace values whose key looks secret with ``***``."""

    if isinstance(obj, dict):
        return {k: ("***" if is_secret_key(k) and v else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact(item) for item in obj)
    return obj


def redact_text(text: str) -> str:
    """Mask inline secret-looking substrings (``sk-...``, ``Bearer ...``)."""

    return _SECRET_VALUE_RE.sub(r"\1***", text)


def safe_event_projection(event) -> dict:
    """A secret-safe, JSON-serializable view of a DomainEvent for streaming (12.9)."""

    payload = redact(dict(event.payload)) if event.payload else {}
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "effect": event.effect.value,
        "state_version": event.state_version,
        "occurred_at": event.occurred_at.isoformat(),
        "task_id": event.task_id,
        "attempt_id": event.attempt_id,
        "plan_version": event.plan_version,
        "gate_id": event.gate_id,
        "payload": payload,
    }
