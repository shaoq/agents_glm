## Context

CR 是知识构建侧增强（索引期），查询侧零改。笔记 §12.1.2 深入讨论了原理与取舍。现有 `OpenAIVisionDescriber`（索引期 LLM + 缓存 + 重试 + 兜底）是 CR context 生成器的同构模板。

## Goals / Non-Goals

**Goals:**
- chunk → 便宜 LLM 生成 context（客观定位）→ indexed_text = context + chunk
- embedding + BM25 用 indexed_text（检索受益）
- Chroma 存原文 text（回传保真）
- 缓存增量（chunk 不变→context 不变）
- 默认关（opt-in）+ 失败兜底（空 context 退化为普通 chunk）

**Non-Goals:**
- 查询侧 contextualize（query 不加 context，笔记 §12.1.2「检索用、回传不用」）
- 全局文档摘要（用 section 级 context，非 summary）
- context 内容概括/评价（约束为客观定位）

## Decisions

**1. indexed_text = context + "\n\n" + text。**
检索（embedding/BM25）用 indexed_text（context 前缀提升语义定位）；回传（Chroma document / context_builder / snippet）用原文 text（保真，context 前缀不污染生成）。

**2. Chroma 存 c.text 不存 indexed_text。**
查询侧 `vector.py` 读 Chroma document → RetrievalResult.text → context_builder → 生成 prompt。若存 indexed_text，context 前缀进生成上下文 → 失真。

**3. 复用 vision_describer 模式。**
`OpenAIContextualizer` 照搬 `OpenAIVisionDescriber`：chat completions + 缓存命中跳过 API + tenacity 重试 + `_NonRetryable` + 失败兜底（返回空串）。

**4. 缓存照搬 ImageDescriptionCache。**
键 = `text_fingerprint(chunk.text) + context_model`。chunk 不变→命中→跳过 LLM。换 model→自动失效（与 EmbeddingCache 换 model/dim 同理）。

**5. 默认关（opt-in）。**
`contextualization_enabled=False`。需显式启用（CR 有 LLM 成本，不是所有场景需要）。关闭时 indexed_text=text，行为不变。

**6. section 级 context（非全局 summary）。**
prompt 输入 = doc_title + section_path + chunk → LLM 生成该 chunk 在 section 语境下的客观定位。复用 chunk 的 section_path（构建侧已写入），零额外解析。

**7. context 约束客观定位。**
prompt 指示「用 50-100 字客观定位此片段在文档中的位置，仅陈述不评价」。禁止概括/评价（降失真）。

## Risks / Trade-offs

- **[CR LLM 成本]** → 便宜模型（Flash）+ 缓存（增量复用）+ 默认关（opt-in）
- **[context 失真]** → 约束客观定位（禁止评价）+ Chroma 存原文（回传保真）
- **[BM25 indexed_text 对齐]** → BM25 用 indexed_text 分词，查询侧 BM25 query 用原 query（query 不 contextualize）→ 对齐无问题（文档侧用 indexed_text 建索引，query 侧原样检索）
- **[并发]** → 多 chunk context 生成可用 ThreadPoolExecutor（仿 Embedder.embed），首版串行（简单）
