## Why

agents_rag 目前只有设计文档与知识笔记，无任何可运行代码，conda `agents_glm` 环境也几乎为空。知识构建（索引管线）是 RAG 的根基——「垃圾进、垃圾出」，查询侧再精巧也救不回构建得烂的知识库；同时它是**独立可交付的子系统**（产出可检索索引，不依赖查询管线）。因此首期优先交付知识构建核心闭环：让一批领域文档能被高质量解析、结构化分块、双索引（向量 + BM25）并支持增量维护。

## What Changes

- 新增项目骨架：`pyproject.toml`（PEP 621 + 入口脚本）+ `src/agents_rag/` 包结构 + `config.py`（pydantic-settings）+ `.env.example`，依赖装到 conda `agents_glm`。
- 新增 **document-parsing**：docling 主力解析 PDF + Office / HTML / Markdown，统一 `Document(sections → blocks)` 模型，`ParserRouter` 降级链 + 解析质量评估 + 清洗归一化。
- 新增 **document-chunking**：结构感知分块（尊重 block 边界、表格 / 代码豁免）+ 父子分块（父块存 KV、子块建索引）。
- 新增 **hybrid-indexing**：智谱 embedding-3（批量 + 并发限流 + 重试 + 缓存）+ Chroma（HNSW）+ BM25（jieba + rank_bm25）双索引，`chunk_id` 对齐 + 元数据 + 父块 KV。
- 新增 **incremental-ingest**：内容指纹（SHA-256 流式 + 预筛）+ 文档注册表（sqlite）+ 五态 diff + 两阶段执行（先建新后删旧 + `status` 中间态）+ CLI `agents-rag ingest`。
- 轻量持久化本轮落地（Chroma 落盘 + embedding 缓存 + 注册表 + BM25 / 父块 pickle）；孤儿清理、崩溃恢复、全量校准等重型工程后置。
- 查询管线（检索 / 重排 / 生成 / 引用）本轮**不实现**。

## Capabilities

### New Capabilities

- `document-parsing`: 多格式文档解析为统一 `Document`（章节树 + 类型化 block + 位置元数据），含降级链、质量评估与清洗归一化。
- `document-chunking`: 将 `Document` 切分为父子分块（结构感知、特殊结构豁免），子块带完整溯源元数据。
- `hybrid-indexing`: 子块向量化（智谱 embedding-3 + 缓存）并写入双索引（Chroma + BM25），`chunk_id` 对齐、父块入 KV。
- `incremental-ingest`: 基于内容指纹与文档注册表的五态增量索引编排 + `agents-rag ingest` CLI。

### Modified Capabilities

（无——`openspec/specs/` 当前为空，本变更为 agents_rag 首批能力。）

## Impact

- **代码**：新建 `src/agents_rag/`（`config` / `models` / `cli` + `ingestion/` + `parsing/` + `cleaning/` + `chunking/` + `indexing/` + `pipeline/`）与 `tests/`。
- **依赖**：conda `agents_glm` 新增 `zhipuai chromadb rank-bm25 jieba docling python-docx python-pptx trafilatura beautifulsoup4 lxml pydantic pydantic-settings typer rich tenacity` + dev（`pytest pytest-asyncio ruff`）；docling 会触发模型下载，首跑较慢。
- **外部 API**：智谱 embedding-3（需 `ZHIPUAI_API_KEY`，缺失 fail-fast）。
- **存储**：`storage/`（gitignore）下 Chroma 持久化目录、`bm25.pkl`、`parents/`、`embedding_cache.sqlite`、`registry.sqlite`。
- **非影响**：不触及查询管线，不引入鉴权 / 服务化，不改既有 `docs/`。
