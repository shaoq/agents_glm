from typing import Any

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class EmbeddingDimensionError(ValueError):
    pass


class OpenAIEmbedder:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        dimension: int,
        max_batch: int = 64,
    ) -> None:
        self.client = client
        self.model = model
        self.dimension = dimension
        self.max_batch = max_batch

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.max_batch):
            batch = texts[offset : offset + self.max_batch]
            response = self._create(batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            for item in ordered:
                vector = list(item.embedding)
                if len(vector) != self.dimension:
                    raise EmbeddingDimensionError(
                        f"expected dimension {self.dimension}, got {len(vector)}"
                    )
                vectors.append(vector)
        return vectors

    @retry(
        retry=retry_if_exception_type(
            (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
        ),
        wait=wait_exponential(min=0.1, max=2),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _create(self, texts: list[str]) -> Any:
        return self.client.embeddings.create(
            input=texts,
            model=self.model,
            dimensions=self.dimension,
        )
