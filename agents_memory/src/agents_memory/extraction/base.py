from typing import Protocol

from agents_memory.models import CandidateMemory, Message


class FactExtractor(Protocol):
    def extract(self, messages: list[Message]) -> list[CandidateMemory]: ...


class FakeFactExtractor:
    def __init__(self, candidates: list[CandidateMemory]) -> None:
        self.candidates = candidates
        self.calls = 0

    def extract(self, messages: list[Message]) -> list[CandidateMemory]:
        self.calls += 1
        return list(self.candidates)
