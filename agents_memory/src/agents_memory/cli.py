import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from openai import OpenAI
from rich.console import Console

from agents_memory.config import Settings
from agents_memory.embedding.openai import OpenAIEmbedder
from agents_memory.extraction.llm import LLMFactExtractor
from agents_memory.models import (
    MemoryScope,
    MemoryType,
    Message,
    PendingResolutionStatus,
    TemporalAnchor,
)
from agents_memory.pipeline.recall import MemoryRecallPipeline
from agents_memory.pipeline.write import MemoryWritePipeline
from agents_memory.processing.candidate import CandidateProcessor
from agents_memory.processing.decision import DecisionEngine
from agents_memory.processing.pending import PendingResolutionPolicy
from agents_memory.recall import RecallRequest, TemporalIntent
from agents_memory.recall.assembly import ContextAssembler
from agents_memory.recall.errors import RecallContractViolation, RecallError
from agents_memory.recall.evidence import (
    EvidenceResolver,
    GLMEventIdentityReviewer,
)
from agents_memory.recall.filtering import EligibilityFilter
from agents_memory.recall.intent import GLMIntentBuilder
from agents_memory.recall.planning import DeterministicPlanner, PlannerConfig
from agents_memory.recall.retrieval import MultiLaneCandidateRetriever
from agents_memory.recall.scoring import GLMBatchReviewer, ScorerConfig, UtilityScorer
from agents_memory.recall.selection import SetSelector
from agents_memory.resolution.llm import LLMRelationResolver
from agents_memory.retrieval.lookup import ContextLookup
from agents_memory.service import MemoryService
from agents_memory.storage.coordinator import StorageCoordinator
from agents_memory.storage.recalls import RecallReadRepository
from agents_memory.storage.repository import MemoryRepository
from agents_memory.storage.vector import ChromaMemoryIndex

console = Console()


def build_runtime() -> tuple[MemoryWritePipeline, MemoryService]:
    settings = Settings()
    client = OpenAI(
        api_key=settings.llm_api_key or "not-configured",
        base_url=settings.llm_base_url,
    )
    repository = MemoryRepository(settings.sqlite_path)
    embedder = OpenAIEmbedder(
        client=client,
        model=settings.embedding_model,
        dimension=settings.embedding_dim,
        max_batch=settings.embedding_max_batch,
    )
    index = ChromaMemoryIndex(
        settings.chroma_path,
        model=settings.embedding_model,
        dimension=settings.embedding_dim,
    )
    coordinator = StorageCoordinator(repository, index, embedder)
    pipeline = MemoryWritePipeline(
        extractor=LLMFactExtractor(client=client, model=settings.memory_extract_model),
        processor=CandidateProcessor(),
        lookup=ContextLookup(
            embedder=embedder,
            index=index,
            repository=repository,
            top_k=settings.memory_lookup_top_k,
            threshold=settings.memory_lookup_threshold,
        ),
        resolver=LLMRelationResolver(
            client=client, model=settings.memory_relation_model
        ),
        decision_engine=DecisionEngine(),
        coordinator=coordinator,
        repository=repository,
        pending_policy=PendingResolutionPolicy(
            high_days=settings.pending_high_ttl_days,
            normal_days=settings.pending_normal_ttl_days,
            low_days=settings.pending_low_ttl_days,
        ),
    )
    return pipeline, MemoryService(repository, coordinator)


def build_read_service() -> MemoryService:
    settings = Settings()
    return MemoryService(MemoryRepository(settings.sqlite_path))


def build_recall_pipeline(
    settings: Settings,
    *,
    client: Any,
    repository: RecallReadRepository,
    embedder: Any,
    index: Any,
) -> MemoryRecallPipeline:
    """Wire the eight Recall stages from settings and shared clients.

    ``client``, ``embedder`` and ``index`` are duck-typed so tests can inject
    fakes; production wiring reuses the same OpenAI/Chroma clients as write.
    """
    return MemoryRecallPipeline(
        intent_builder=GLMIntentBuilder(client=client, model=settings.memory_recall_model),
        planner=DeterministicPlanner(
            config=PlannerConfig(
                session_quota=settings.recall_session_quota,
                agent_history_quota=settings.recall_agent_history_quota,
                user_shared_quota=settings.recall_user_shared_quota,
                global_candidate_limit=settings.recall_global_candidate_limit,
            )
        ),
        retriever=MultiLaneCandidateRetriever(
            embedder=embedder, index=index, repository=repository
        ),
        filter=EligibilityFilter(repository=repository),
        scorer=UtilityScorer(
            config=ScorerConfig(llm_review_top_n=settings.recall_llm_review_top_n),
            reviewer=GLMBatchReviewer(
                client=client,
                model=settings.memory_recall_model,
                top_n=settings.recall_llm_review_top_n,
            ),
        ),
        resolver=EvidenceResolver(
            repository=repository,
            reviewer=GLMEventIdentityReviewer(
                client=client, model=settings.memory_recall_model
            ),
        ),
        selector=SetSelector(),
        assembler=ContextAssembler(),
        repository=repository,
    )


def build_recall_service(settings: Settings | None = None) -> MemoryService:
    """Build a MemoryService with a real Recall pipeline when configured.

    Recall configuration is validated lazily: without LLM_API_KEY the service
    is returned without a recall pipeline so storage-only commands still work
    and recall reports "not configured" on demand.
    """
    settings = settings or Settings()
    repository = MemoryRepository(settings.sqlite_path)
    try:
        settings.validate_recall()
    except ValueError:
        return MemoryService(repository)
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    embedder = OpenAIEmbedder(
        client=client,
        model=settings.embedding_model,
        dimension=settings.embedding_dim,
        max_batch=settings.embedding_max_batch,
    )
    index = ChromaMemoryIndex(
        settings.chroma_path,
        model=settings.embedding_model,
        dimension=settings.embedding_dim,
    )
    recall_repository = RecallReadRepository(settings.sqlite_path)
    pipeline = build_recall_pipeline(
        settings,
        client=client,
        repository=recall_repository,
        embedder=embedder,
        index=index,
    )
    return MemoryService(repository, recall_pipeline=pipeline)


def _render_recall(result: Any, *, json_output: bool, diagnostic: bool) -> None:
    if json_output:
        typer.echo(result.model_dump_json())
        return
    metadata = result.metadata
    console.print(
        f"[bold]{metadata.sufficiency.value}[/bold] "
        f"({metadata.execution_status.value}) "
        f"evidence={len(result.evidence)}"
    )
    if result.context:
        console.print(result.context)
    if diagnostic:
        for code in metadata.degradations:
            console.print(f"- degrade: {code.value}")
        for note in metadata.diagnostics:
            console.print(f"- diag: {note}")


def create_app(
    pipeline: MemoryWritePipeline | Any | None = None,
    service: MemoryService | Any | None = None,
) -> typer.Typer:
    application = typer.Typer(help="Agent memory write and recall pipeline")
    sync_app = typer.Typer(help="Repair or rebuild the semantic index")
    pending_app = typer.Typer(help="Observe and maintain deferred resolutions")
    runtime: tuple[Any, Any] | None = None
    read_service_cache: Any | None = service
    recall_service_cache: Any | None = service

    def components() -> tuple[Any, Any]:
        nonlocal runtime
        if pipeline is not None and service is not None:
            return pipeline, service
        if runtime is None:
            runtime = build_runtime()
        return runtime

    def read_service() -> Any:
        nonlocal read_service_cache
        if read_service_cache is None:
            read_service_cache = build_read_service()
        return read_service_cache

    def recall_service() -> Any:
        nonlocal recall_service_cache
        if recall_service_cache is None:
            recall_service_cache = build_recall_service()
        return recall_service_cache

    @application.command()
    def write(
        path: Path | None = typer.Argument(None),
        json_output: bool = typer.Option(False, "--json-output"),
    ) -> None:
        if pipeline is None:
            Settings().validate_write()
        raw = (
            path.read_text(encoding="utf-8")
            if path is not None
            else typer.get_text_stream("stdin").read()
        )
        payload = json.loads(raw)
        write_pipeline, _ = components()
        report = write_pipeline.write(
            request_id=payload["request_id"],
            scope=MemoryScope.model_validate(payload["scope"]),
            messages=[Message.model_validate(item) for item in payload["messages"]],
        )
        if json_output:
            typer.echo(report.model_dump_json())
        else:
            console.print(
                f"[bold]{report.status.value}[/bold] "
                f"request={report.request_id} results={len(report.results)}"
            )
            for result in report.results:
                console.print(
                    f"- {result.action.value}: {result.content} "
                    f"(memory_id={result.memory_id or '-'})"
                )

    @application.command("list")
    def list_command(
        user_id: str = typer.Option(..., "--user-id"),
        agent_id: str | None = typer.Option(None, "--agent-id"),
        session_id: str | None = typer.Option(None, "--session-id"),
        type: MemoryType | None = typer.Option(None, "--type"),
        history: bool = typer.Option(False, "--history"),
        json_output: bool = typer.Option(False, "--json-output"),
    ) -> None:
        memory_service = read_service()
        records = memory_service.list_memories(
            MemoryScope(
                user_id=user_id, agent_id=agent_id, session_id=session_id
            ),
            type,
            include_history=history,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    [record.model_dump(mode="json") for record in records],
                    ensure_ascii=False,
                )
            )
        else:
            for record in records:
                console.print(
                    f"{record.id} [{record.type.value}/{record.validity.value}] "
                    f"{record.content}"
                )

    @application.command()
    def show(
        memory_id: str,
        user_id: str = typer.Option(..., "--user-id"),
    ) -> None:
        memory_service = read_service()
        detail = memory_service.get_memory(memory_id, user_id)
        if detail is None:
            raise typer.Exit(code=1)
        typer.echo(detail.model_dump_json())

    @application.command()
    def delete(
        memory_id: str,
        user_id: str = typer.Option(..., "--user-id"),
    ) -> None:
        _, memory_service = components()
        if not memory_service.delete_memory(memory_id, user_id):
            raise typer.Exit(code=1)
        typer.echo(f"deleted {memory_id}")

    @sync_app.command()
    def repair() -> None:
        _, memory_service = components()
        typer.echo(f"repaired {memory_service.repair_index()} request(s)")

    @sync_app.command()
    def rebuild() -> None:
        _, memory_service = components()
        typer.echo(f"rebuilt {memory_service.rebuild_index()} memory vector(s)")

    @pending_app.command("list")
    def pending_list(
        user_id: str = typer.Option(..., "--user-id"),
        agent_id: str | None = typer.Option(None, "--agent-id"),
        session_id: str | None = typer.Option(None, "--session-id"),
        status: PendingResolutionStatus | None = typer.Option(
            PendingResolutionStatus.OPEN, "--status"
        ),
        json_output: bool = typer.Option(False, "--json-output"),
    ) -> None:
        records = read_service().list_pending_resolutions(
            MemoryScope(
                user_id=user_id, agent_id=agent_id, session_id=session_id
            ),
            status,
        )
        payload = [record.model_dump(mode="json") for record in records]
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False))
        else:
            for record in records:
                console.print(
                    f"{record.resolution_id} [{record.status.value}] "
                    f"age={record.age_seconds}s importance={record.importance} "
                    f"expires={record.expires_at.isoformat()} {record.reason}"
                )

    @pending_app.command("sweep")
    def pending_sweep() -> None:
        typer.echo(
            f"updated {read_service().sweep_pending_resolutions()} "
            "pending resolution(s)"
        )

    @pending_app.command("cleanup")
    def pending_cleanup(
        before: datetime = typer.Option(..., "--before"),
    ) -> None:
        typer.echo(
            f"deleted {read_service().cleanup_pending_resolutions(before=before)} "
            "terminal pending resolution(s)"
        )

    @application.command()
    def recall(
        query: str = typer.Argument(..., help="Recall query text."),
        user_id: str = typer.Option(..., "--user-id"),
        agent_id: str | None = typer.Option(None, "--agent-id"),
        session_id: str | None = typer.Option(None, "--session-id"),
        type: list[MemoryType] | None = typer.Option(
            None, "--type", help="Restrict to memory types (repeatable)."
        ),
        temporal: TemporalIntent | None = typer.Option(
            None,
            "--temporal",
            help="Temporal view: current_state|point_in_time|interval|evolution.",
        ),
        time_start: datetime | None = typer.Option(None, "--time-start"),
        time_end: datetime | None = typer.Option(None, "--time-end"),
        max_evidence: int = typer.Option(10, "--max-evidence"),
        max_tokens: int = typer.Option(2000, "--max-tokens"),
        allow_agent_history: bool = typer.Option(
            True, "--agent-history/--no-agent-history"
        ),
        allow_user_shared: bool = typer.Option(True, "--user-shared/--no-user-shared"),
        diagnostic: bool = typer.Option(False, "--diagnostic"),
        json_output: bool = typer.Option(False, "--json-output"),
    ) -> None:
        explicit_time_range: TemporalAnchor | None = None
        if time_start is not None and time_end is not None:
            explicit_time_range = TemporalAnchor(start=time_start, end=time_end)
        request = RecallRequest(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            query=query,
            explicit_types=tuple(type) if type else (),
            explicit_time_range=explicit_time_range,
            temporal_intent=temporal,
            allow_agent_history=allow_agent_history,
            allow_user_shared=allow_user_shared,
            max_evidence_items=max_evidence,
            max_context_tokens=max_tokens,
            diagnostic=diagnostic,
        )
        try:
            result = recall_service().recall(request)
        except RecallContractViolation as exc:
            console.print(f"[red]recall not available:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        except RecallError as exc:
            console.print(f"[red]recall failed:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        _render_recall(result, json_output=json_output, diagnostic=diagnostic)

    application.add_typer(sync_app, name="sync")
    application.add_typer(pending_app, name="pending")
    return application


app = create_app()
