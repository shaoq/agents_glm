import re
import unicodedata

from pydantic import BaseModel, ConfigDict

from agents_memory.extraction.llm import LLMFactExtractor, SourceAttributionError
from agents_memory.models import CandidateMemory, Message


class CandidateBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidates: tuple[CandidateMemory, ...]
    filtered_count: int


class CandidateProcessor:
    def process(
        self, candidates: list[CandidateMemory], messages: list[Message]
    ) -> CandidateBatch:
        LLMFactExtractor._validate_sources(candidates, messages)
        seen: set[tuple[str, str]] = set()
        accepted: list[CandidateMemory] = []
        for candidate in candidates:
            normalized = self.normalize(candidate.content)
            key = candidate.type.value, normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            accepted.append(candidate.model_copy(update={"content": normalized}))
        return CandidateBatch(
            candidates=tuple(accepted),
            filtered_count=len(candidates) - len(accepted),
        )

    @staticmethod
    def normalize(content: str) -> str:
        value = unicodedata.normalize("NFKC", content)
        return re.sub(r"\s+", " ", value).strip()


__all__ = ["CandidateBatch", "CandidateProcessor", "SourceAttributionError"]
