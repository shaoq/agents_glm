## Why

知识构建侧已完整交付（核心闭环 + 嵌套树 + 图片索引 + 诊断式降级），双索引（Chroma + BM25）、父子 KV、元数据全部就绪。但当前只有索引能力（`agents-rag ingest`），**无法提问**——需要查询管线把「用户问题」变成「带引用的可信回答」，完成 RAG 的闭环。

## What Changes

- 新增 `retrieval/`：向量召回 + BM25 召回 + RRF 融合 + 智谱 Rerank 精排（消费已建双索引）
- 新增 `generation/`：ContextBuilder（去重 + token 预算 + 引用编号注入）+ Generator（GLM-4.5 四约束）+ prompts
- 新增 `citation/`：CitationChecker（引用编号校验 + 剔除无效）+ Citation 溯源
- 新增 `pipeline/query.py`：QueryPipeline 编排（8 步：embed→双路→RRF→rerank→AutoMerging→context→generate→check）
- 新增 `cli.py` 的 `ask` 子命令
- 补 `models.py`：RetrievalResult / Citation / Answer
- 补 `config.py` + `.env.example`：LLM_MODEL / RERANK_MODEL / VECTOR_TOP_K / BM25_TOP_K / RERANK_TOP_N / LLM_MAX_CONTEXT_TOKENS
- 补 `pyproject.toml`：tiktoken（token 预算）
- 检索空兜底（召回=0 → 不生成，直接返回「未找到」）
- AutoMerging 父子回传（rerank 后按 parent_id 分组，≥threshold 回传父块）

## Capabilities

### New Capabilities

- `query-pipeline`: 在线查询管线——用户问题 → 双路召回 → RRF 融合 → Rerank 精排 → 上下文构建 → GLM 生成（强制引用）→ 引用校验 → 带引用来源的可信回答

### Modified Capabilities

（无——查询管线是全新能力，不修改现有构建侧 spec）

## Impact

- **代码**：新建 `retrieval/`（5 文件）+ `generation/`（3 文件）+ `citation/`（2 文件）+ `pipeline/query.py`；补 `models.py` / `config.py` / `cli.py` / `pyproject.toml`
- **复用现有**：`Embedder.embed`（query 向量化）、`ChromaStore.query`（向量召回）、`BM25Index.query`（BM25 召回）、`ParentStore.get`（AutoMerging）、`text_fingerprint`（去重）、`vision_describer` 的 retry 客户端模式（Generator/Reranker 复用）
- **依赖**：新增 `tiktoken`；复用 `openai>=1`
- **外部 API**：智谱 Rerank API（RERANK_MODEL）+ GLM-4.5 生成（LLM_MODEL），走 OpenAI 兼容端点
- **非影响**：构建侧代码零改；查询改写/HyDE、RAGAS、置信度聚合、多轮 后置
