"""CLI 入口（Typer）：``agents-rag ingest <dir>``。

Rich 输出五态统计 + 索引规模 + 失败日志。
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agents_rag.chunking.parent_child import StructuralChunker
from agents_rag.cleaning.normalizer import Normalizer
from agents_rag.config import settings
from agents_rag.ingestion.registry import DocumentRegistry
from agents_rag.indexing.bm25_index import BM25Index
from agents_rag.indexing.cache import EmbeddingCache
from agents_rag.indexing.chroma_store import ChromaStore
from agents_rag.indexing.contextualizer import ContextCache, OpenAIContextualizer
from agents_rag.indexing.embedder import OpenAIEmbedder
from agents_rag.indexing.image_store import ImageStore
from agents_rag.indexing.parent_store import ParentStore
from agents_rag.indexing.vision_describer import ImageDescriptionCache, OpenAIVisionDescriber
from agents_rag.parsing.router import ParserRouter
from agents_rag.pipeline.ingest import IngestPipeline, IngestReport
from agents_rag.pipeline.query import QueryPipeline
from agents_rag.retrieval.hybrid import HybridRetriever
from agents_rag.retrieval.vector import VectorRetriever
from agents_rag.retrieval.bm25 import BM25Retriever
from agents_rag.retrieval.reranker import ZhipuReranker
from agents_rag.generation.context_builder import ContextBuilder
from agents_rag.generation.llm import OpenAIGenerator
from agents_rag.citation.checker import CitationChecker
from agents_rag.citation.faithfulness import FaithfulnessChecker
from agents_rag.models import Answer

app = typer.Typer(help="agents_rag 知识构建（索引管线）CLI")
console = Console()


@app.callback()
def _main() -> None:
    """agents_rag 知识构建（索引管线）CLI。"""


@app.command()
def ingest(
    directory: str = typer.Argument(..., help="待索引的文档目录"),
    no_pdf: bool = typer.Option(False, "--no-pdf", help="禁用 PDF(docling) 解析"),
) -> None:
    """扫描目录并构建/更新知识库索引（五态增量）。"""
    settings.ensure_storage_dirs()
    api_key = settings.require_api_key()  # 缺失 fail-fast

    bm25_path = settings.bm25_path
    bm25 = BM25Index.load(bm25_path) if bm25_path.exists() else BM25Index()

    with (
        DocumentRegistry(settings.registry_path) as registry,
        EmbeddingCache(settings.embedding_cache_path) as cache,
        ImageStore(settings.images_dir) as image_store,
        ImageDescriptionCache(settings.storage_dir / "image_descriptions.sqlite") as desc_cache,
        ContextCache(settings.context_cache_path) as ctx_cache,
    ):
        contextualizer = (
            OpenAIContextualizer(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.contextualization_model,
                max_tokens=settings.contextualization_max_tokens,
            )
            if settings.contextualization_enabled
            else None
        )
        pipe = IngestPipeline(
            registry=registry,
            router=ParserRouter.with_defaults(enable_pdf=not no_pdf),
            normalizer=Normalizer(),
            chunker=StructuralChunker(
                parent_max_size=settings.parent_max_size,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
            ),
            embedder=OpenAIEmbedder(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                max_batch=settings.embedding_max_batch,
                max_concurrency=settings.embedding_max_concurrency,
            ),
            cache=cache,
            vector_store=ChromaStore(settings.chroma_dir),
            bm25=bm25,
            parent_store=ParentStore(settings.parents_dir),
            image_store=image_store,
            vision_describer=OpenAIVisionDescriber(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.vision_model,
            ),
            description_cache=desc_cache,
            contextualizer=contextualizer,
            context_cache=ctx_cache if contextualizer else None,
        )
        report = pipe.run(directory)
        bm25.save(bm25_path)

    _print_report(report)


def _print_report(report: IngestReport) -> None:
    console.print(f"[bold green]索引完成[/] · 子块总数 {report.indexed_chunks}")
    table = Table("状态", "数量")
    for kind in ("new", "update", "delete", "move"):
        table.add_row(kind, str(report.counts.get(kind, 0)))
    table.add_row("skip", str(report.counts.get("skipped", 0)))
    table.add_row("[red]failed", str(report.counts.get("failed", 0)))
    console.print(table)
    for doc_id, err in report.failed:
        console.print(f"  [red]{doc_id}[/]: {err}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="提问内容"),
) -> None:
    """基于已建索引回答问题（带引用来源）。"""
    settings.ensure_storage_dirs()
    api_key = settings.require_api_key()

    bm25 = BM25Index.load(settings.bm25_path) if settings.bm25_path.exists() else BM25Index()
    with (
        DocumentRegistry(settings.registry_path),
        EmbeddingCache(settings.embedding_cache_path) as cache,
    ):
        embedder = OpenAIEmbedder(
            api_key=api_key,
            base_url=settings.llm_base_url,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
        store = ChromaStore(settings.chroma_dir)
        faithfulness_checker = (
            FaithfulnessChecker(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.faithfulness_model,
            )
            if settings.faithfulness_enabled
            else None
        )
        pipe = QueryPipeline(
            hybrid_retriever=HybridRetriever(
                VectorRetriever(embedder, store),
                BM25Retriever(bm25, store),
            ),
            reranker=ZhipuReranker(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.rerank_model,
            ),
            context_builder=ContextBuilder(
                parent_store=ParentStore(settings.parents_dir),
                max_tokens=settings.llm_max_context_tokens,
            ),
            generator=OpenAIGenerator(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
            ),
            citation_checker=CitationChecker(),
            faithfulness_checker=faithfulness_checker,
            confidence_enabled=settings.confidence_enabled,
            confidence_threshold=settings.confidence_threshold,
            confidence_weight_rerank=settings.confidence_weight_rerank,
            confidence_weight_citation=settings.confidence_weight_citation,
            confidence_weight_faithfulness=settings.confidence_weight_faithfulness,
            vector_top_k=settings.vector_top_k,
            bm25_top_k=settings.bm25_top_k,
            rerank_top_n=settings.rerank_top_n,
        )
        answer = pipe.ask(question)

    _print_answer(answer)


def _print_answer(answer: Answer) -> None:
    if answer.status.value == "no_result":
        console.print(f"[yellow]未找到[/] · {answer.message}")
        return
    if answer.status.value == "low_confidence":
        console.print(f"[yellow]⚠ 低置信度 · 仅供参考[/]")
    console.print(f"[bold green]回答[/]\n{answer.text}\n")
    if answer.faithfulness_score is not None:
        console.print(f"[dim]faithfulness: {answer.faithfulness_score:.2f}[/]")
    if answer.citations:
        console.print("[bold]引用来源[/]")
        for i, c in enumerate(answer.citations, 1):
            page_str = f"第{c.page}页" if c.page else "页码未知"
            console.print(f"  [{i}] {c.source_name} · {page_str}")
            console.print(f"      {c.snippet}")


if __name__ == "__main__":
    app()
