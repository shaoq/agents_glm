"""Structured JSONL logging correlated by Run/Task/Attempt/Operation/Gate/Plan
IDs (task 12.7). Records are secret-redacted before they leave the process."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from agents_orchestration.observability.redaction import redact


class JSONLLogger:
    def __init__(self, stream: TextIO | None = None, *, clock=None) -> None:
        self.stream = stream or sys.stderr
        self.clock = clock

    def log(
        self,
        *,
        event: str,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        operation_id: str | None = None,
        gate_id: str | None = None,
        plan_version: int | None = None,
        **fields: object,
    ) -> dict:
        record = {
            "ts": self.clock.now().isoformat() if self.clock else None,
            "event": event,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "operation_id": operation_id,
            "gate_id": gate_id,
            "plan_version": plan_version,
        }
        record.update(redact(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        self.stream.write(line + "\n")
        return record
