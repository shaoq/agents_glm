import json
from typing import Any

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents_memory.extraction.prompts import EXTRACTION_SYSTEM_PROMPT
from agents_memory.models import CandidateMemory, Message, SourceKind
from agents_memory.processing.temporal import normalize_temporal_anchor


class ExtractionOutputError(ValueError):
    pass


class SourceAttributionError(ValueError):
    pass


class _ExtractionEnvelope(BaseModel):
    candidates: list[CandidateMemory]


class LLMFactExtractor:
    def __init__(self, *, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def extract(self, messages: list[Message]) -> list[CandidateMemory]:
        payload = [
            {"message_id": item.message_id, "role": item.role, "content": item.content}
            for item in messages
        ]
        last_error: Exception | None = None
        envelope: _ExtractionEnvelope | None = None
        for attempt in range(2):
            response = self._create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                    *(
                        [
                            {
                                "role": "system",
                                "content": "上次输出无法解析，请仅返回符合 schema 的 JSON。",
                            }
                        ]
                        if attempt
                        else []
                    ),
                ],
            )
            try:
                content = response.choices[0].message.content
                envelope = _ExtractionEnvelope.model_validate_json(content)
                break
            except (ValidationError, ValueError, TypeError, IndexError) as exc:
                last_error = exc
        if envelope is None:
            raise ExtractionOutputError("invalid extraction output") from last_error
        self._validate_sources(envelope.candidates, messages)
        return self._normalize_event_times(envelope.candidates, messages)

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

    @staticmethod
    def _validate_sources(
        candidates: list[CandidateMemory], messages: list[Message]
    ) -> None:
        by_id = {message.message_id: message for message in messages}
        if len(by_id) != len(messages):
            raise SourceAttributionError("message_id must be unique within a request")
        for candidate in candidates:
            supporting = []
            for message_id in candidate.source_message_ids:
                message = by_id.get(message_id)
                if message is None:
                    raise SourceAttributionError(f"unknown source message: {message_id}")
                supporting.append(message)
            if candidate.source_kind in (
                SourceKind.USER_EXPLICIT,
                SourceKind.USER_CONFIRMED,
            ) and not any(message.role == "user" for message in supporting):
                raise SourceAttributionError("user fact must be supported by a user message")
            if candidate.source_kind is SourceKind.USER_EXPLICIT and any(
                message.role != "user" for message in supporting
            ):
                raise SourceAttributionError(
                    "user_explicit sources must contain only user messages"
                )
            if candidate.source_kind is SourceKind.TOOL_VERIFIED and not any(
                message.role == "tool" for message in supporting
            ):
                raise SourceAttributionError("tool fact must be supported by a tool message")

    @staticmethod
    def _normalize_event_times(
        candidates: list[CandidateMemory], messages: list[Message]
    ) -> list[CandidateMemory]:
        by_id = {message.message_id: message for message in messages}
        normalized: list[CandidateMemory] = []
        for candidate in candidates:
            frame = candidate.event_frame
            raw_text = frame.temporal_anchor.raw_text if frame else None
            if frame is None or not raw_text:
                normalized.append(candidate)
                continue
            reference = next(
                (
                    by_id[message_id].occurred_at
                    for message_id in candidate.source_message_ids
                    if by_id[message_id].occurred_at is not None
                ),
                None,
            )
            anchor = normalize_temporal_anchor(raw_text, reference)
            normalized.append(
                candidate.model_copy(
                    update={"event_frame": frame.model_copy(
                        update={"temporal_anchor": anchor}
                    )}
                )
            )
        return normalized
