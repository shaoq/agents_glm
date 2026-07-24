from types import SimpleNamespace

import pytest

from agents_memory.embedding.base import FakeEmbedder
from agents_memory.embedding.openai import EmbeddingDimensionError, OpenAIEmbedder


def test_fake_embedder_is_deterministic() -> None:
    embedder = FakeEmbedder(dimension=3)

    assert embedder.embed(["same"]) == embedder.embed(["same"])
    assert embedder.embed(["same"]) != embedder.embed(["different"])


def test_openai_embedder_batches_and_preserves_order() -> None:
    calls: list[list[str]] = []

    class Embeddings:
        def create(self, *, input: list[str], model: str, dimensions: int):
            calls.append(input)
            start = sum(len(batch) for batch in calls[:-1])
            data = [
                SimpleNamespace(index=index, embedding=[float(start + index), 1.0])
                for index in reversed(range(len(input)))
            ]
            return SimpleNamespace(data=data)

    client = SimpleNamespace(embeddings=Embeddings())
    embedder = OpenAIEmbedder(
        client=client, model="embedding-3", dimension=2, max_batch=2
    )

    assert embedder.embed(["a", "b", "c"]) == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]
    assert calls == [["a", "b"], ["c"]]


def test_openai_embedder_rejects_wrong_dimension() -> None:
    response = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0])])
    client = SimpleNamespace(
        embeddings=SimpleNamespace(create=lambda **_: response)
    )
    embedder = OpenAIEmbedder(client=client, model="embedding-3", dimension=2)

    with pytest.raises(EmbeddingDimensionError):
        embedder.embed(["a"])
