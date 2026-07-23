## Why

当前 chunk 直接 embedding/BM25，缺上下文定位——短 chunk 脱离文档语境时语义模糊（如「支持 2048 维」脱离「embedding-3 模型说明」上下文，检索时难以精准命中）。Contextual Retrieval（CR）给每个 chunk 用便宜 LLM 生成客观定位前缀（50-100 字），拼接后 embedding/BM25。Anthropic 实测累计降检索失败率 ~67%（与 BM25 + rerank 正交叠加）。

## What Changes

- 新增 `indexing/contextualizer.py`：`ContextCache`（sqlite，键=text_hash+model）+ `OpenAIContextualizer`（chat completions + 缓存 + 重试 + 兜底）
- `ChildChunk` 加 `context: str = ""` + `@property indexed_text`（context + "\n\n" + text）
- pipeline 插入 CR 阶段（chunk 后、embed 前）：对每个 child 调 contextualizer 生成 context
- embedding + BM25 改用 `indexed_text`（检索用 context+chunk）
- **Chroma document 保持存原文 `c.text`**（回传/snippet 保真，查询侧零改）
- config 加开关（`contextualization_enabled=False` 默认关）+ 便宜模型 + 缓存路径
- 失败兜底：context="" → indexed_text=text → 退化为普通 chunk

## Capabilities

### New Capabilities

- `contextual-retrieval`: 索引期为每个 chunk 生成客观定位前缀（context），拼接为 indexed_text 供 embedding/BM25；原文 chunk 供回传/生成。缓存增量复用（chunk 不变→context 不变）。

### Modified Capabilities

（无——CR 通过 `indexed_text` property 在实现层影响 embedding/BM25 输入，不改现有 spec 的 requirement 语义）

## Impact

- **新建**：`indexing/contextualizer.py`（ContextCache + OpenAIContextualizer）
- **改动**：`models.py`（ChildChunk + context/indexed_text）、`config.py`（CR 配置）、`pipeline/ingest.py`（CR 阶段 + embed 输入）、`bm25_index.py`（indexed_text）、`cli.py`（条件构造）
- **不动**：`chroma_store.py`（保持 c.text）、查询侧全部（query/vector/bm25/context_builder/reranker）
- **依赖**：复用 `openai>=1`（便宜 LLM chat completions）
- **外部 API**：便宜 LLM（默认 glm-4-flash），走 OpenAI 兼容端点
