# Implementation Tasks

> 查询管线核心闭环：检索→生成→引用。消费已建双索引。
> 实施依据：[proposal](./proposal.md) · [design](./design.md) · [specs](./specs/) · [实施设计](../../../agents_rag/docs/specs/2026-07-23-query-pipeline-implementation.md)

## 1. models + config + 依赖

- [ ] 1.1 `models.py` 补 `RetrievalResult` / `Citation` / `AnswerStatus` / `Answer`（pydantic frozen）
- [ ] 1.2 `config.py` + `.env.example` 补 6 个查询参数（LLM_MODEL / RERANK_MODEL / VECTOR_TOP_K / BM25_TOP_K / RERANK_TOP_N / LLM_MAX_CONTEXT_TOKENS）
- [ ] 1.3 `pyproject.toml` 加 `tiktoken>=0.7`

## 2. retrieval/（检索 + 融合 + 重排）

- [ ] 2.1 `retrieval/base.py`：`Retriever` ABC（`retrieve(query, k) → list[RetrievalResult]`）
- [ ] 2.2 `retrieval/vector.py`：`VectorRetriever`（`Embedder.embed([query])` → `ChromaStore.query(vec, k, where={"status":"active"})` → 包装为 RetrievalResult）
- [ ] 2.3 `retrieval/bm25.py`：`BM25Retriever`（`BM25Index.query(text, k)` → 包装为 RetrievalResult）
- [ ] 2.4 `retrieval/hybrid.py`：`HybridRetriever`（RRF k=60，rank 倒数求和，两路融合）
- [ ] 2.5 `retrieval/reranker.py`：`Reranker` ABC + 智谱 API 实现（OpenAI 兼容，tenacity 重试，`_NonRetryable` 复用）
- [ ] 2.6 单测：RRF 融合（两路排名对齐 + 倒数求和）、VectorRetriever（Fake embedder + tmp Chroma）、BM25Retriever

## 3. generation/（上下文 + 生成）

- [ ] 3.1 `generation/prompts.py`：system prompt 四约束 + `CITATION_FORMAT` 共享常量（三方契约）
- [ ] 3.2 `generation/context_builder.py`：`ContextBuilder.build(results, query) → (context_str, id_map)`（hash 去重 + 父子去重 + token 预算截断 + `[N]（文档名,页码）` 注入）
- [ ] 3.3 `generation/llm.py`：`Generator` ABC + GLM 实现（OpenAI 兼容 chat completions，温度 0.3，retry 复用 vision_describer 模式）
- [ ] 3.4 单测：ContextBuilder 去重/预算/编号格式、Generator（Fake mock chat completions）

## 4. citation/（引用溯源 + 校验）

- [ ] 4.1 `citation/sources.py`：从 RetrievalResult.metadata 构造 `Citation`（doc_id / source_name / page / snippet）
- [ ] 4.2 `citation/checker.py`：`CitationChecker.check(answer_text, context_ids) → Answer`（正则 `\[(\d+)\]` 提取 → 集合比对 → 无效剔除 + Citation 构造）
- [ ] 4.3 单测：CitationChecker（有效/无效/漏标各场景）

## 5. pipeline/query.py（查询编排）

- [ ] 5.1 `QueryPipeline.ask(query) → Answer`：串联 embed→双路→RRF→rerank→AutoMerging→context→generate→check；检索空兜底
- [ ] 5.2 AutoMerging 实现（rerank 后按 parent_id 分组，≥threshold 回传 ParentStore.get）
- [ ] 5.3 集成测试：FakeEmbedder + FakeReranker + FakeGenerator + tmp_path → 端到端 ask → 断言 Answer + 引用
- [ ] 5.4 集成测试：检索空 → status=NO_RESULT

## 6. CLI + 端到端

- [ ] 6.1 `cli.py` 加 `ask` 子命令（参照 ingest 资源初始化 + Rich 输出回答 + 引用列表）
- [ ] 6.2 端到端：先 ingest fixtures → `agents-rag ask "GLM-4.5 支持多少维 embedding？"` → 验证带引用回答
- [ ] 6.3 覆盖率 ≥ 80%
