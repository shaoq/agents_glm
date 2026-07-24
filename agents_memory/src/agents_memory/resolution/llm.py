import json
from typing import Any

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents_memory.models import (
    CandidateMemory,
    MemoryRecord,
    RelationKind,
    RelationMatch,
)
from agents_memory.resolution.prompts import RELATION_SYSTEM_PROMPT


class RelationOutputError(ValueError):
    pass


class _RelationEnvelope(BaseModel):
    relations: list[RelationMatch]


class LLMRelationResolver:
    semantic_relations = {
        RelationKind.DUPLICATE,
        RelationKind.SUPPLEMENT,
        RelationKind.CONTRADICT,
        RelationKind.CORRECT,
        RelationKind.NONE,
    }

    def __init__(self, *, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def resolve(
        self, candidate: CandidateMemory, histories: list[MemoryRecord]
    ) -> list[RelationMatch]:
        if not histories:
            return []
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "histories": [
                {"id": record.id, "content": record.content, "type": record.type.value}
                for record in histories
            ],
        }
        last_error: Exception | None = None
        allowed = {record.id for record in histories}
        for attempt in range(2):
            response = self._create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": RELATION_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    *(
                        [{"role": "system", "content": "请修复为合法 JSON schema。"}]
                        if attempt
                        else []
                    ),
                ],
            )
            try:
                envelope = _RelationEnvelope.model_validate_json(
                    response.choices[0].message.content
                )
                seen: set[str] = set()
                for relation in envelope.relations:
                    if (
                        relation.memory_id not in allowed
                        or relation.memory_id in seen
                        or relation.relation not in self.semantic_relations
                    ):
                        raise RelationOutputError(
                            "relation output references invalid memory"
                        )
                    seen.add(relation.memory_id)
                if seen != allowed:
                    raise RelationOutputError(
                        "relation output must cover every history"
                    )
                return envelope.relations
            except (
                ValidationError,
                ValueError,
                TypeError,
                IndexError,
                RelationOutputError,
            ) as exc:
                last_error = exc
        raise RelationOutputError("invalid relation output") from last_error

    @retry(
        retry=retry_if_exception_type(
            (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
        ),
        wait=wait_exponential(min=0.01, max=1),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _create(self, **kwargs: Any) -> Any:
        return self.client.chat.completions.create(**kwargs)
