"""Execution-time diagnostics accumulator shared across Recall stages.

Stage results stay immutable (``FrozenModel``); this object only collects
degradations and notes that the pipeline later folds into the frozen
``RecallMetadata``. Living in the recall package keeps the dependency
direction correct: the pipeline depends on recall stages, not the reverse.
"""

from agents_memory.recall.models import DegradationCode


class RecallDiagnostics:
    """Mutable accumulator for degradations and notes during one Recall run."""

    __slots__ = ("_degradations", "_notes")

    def __init__(self) -> None:
        self._degradations: list[DegradationCode] = []
        self._notes: list[str] = []

    def degrade(self, code: DegradationCode, note: str = "") -> None:
        if code not in self._degradations:
            self._degradations.append(code)
        if note:
            self._notes.append(f"{code.value}: {note}")

    def note(self, text: str) -> None:
        if text:
            self._notes.append(text)

    @property
    def degradations(self) -> tuple[DegradationCode, ...]:
        return tuple(self._degradations)

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self._notes)
