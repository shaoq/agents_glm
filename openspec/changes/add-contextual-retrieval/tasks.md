# Implementation Tasks

> CR：chunk → 便宜 LLM 生成 context → indexed_text = context+chunk → embed/BM25 用 indexed_text，Chroma 存原文。
> 默认关（opt-in），失败兜底空串退化。查询侧零改。

## 1. models + config

- [x] 1.1 `models.py`：ChildChunk 加 `context: str = ""` + `@property indexed_text`（context+"\n\n"+text 或 text）
- [x] 1.2 `config.py` + `.env.example`：加 `contextualization_enabled`(False) / `contextualization_model`(glm-4-flash) / `contextualization_max_tokens`(150) / `contextualization_max_concurrency`(8) + `context_cache_path` property

## 2. contextualizer（新模块）

- [x] 2.1 `indexing/contextualizer.py`：`ContextCache`（照搬 ImageDescriptionCache：sqlite，键=text_hash+model）+ `OpenAIContextualizer`（照搬 OpenAIVisionDescriber：chat completions + 缓存命中跳过 + tenacity 重试 + 失败兜底空串）+ `_CONTEXTUALIZE_PROMPT`（客观定位 50-100 字，禁评价）
- [x] 2.2 单测：缓存命中/模型变更/失败兜底（FakeContextualizer + mock chat completions）

## 3. pipeline CR 阶段

- [x] 3.1 `ingest.py` `__init__` 加 `contextualizer`/`context_cache` 可选参数；`_apply_new_or_update` chunk 后 embed 前插 `if self.contextualizer: children = self._contextualize_children(children, document)`
- [x] 3.2 `_contextualize_children`（仿 `_process_images`：对每个 child 调 contextualize + model_copy(update={"context":...})）
- [x] 3.3 embed 输入 `[c.text...]` → `[c.indexed_text...]`
- [x] 3.4 `bm25_index.py` upsert：`tokenize(c.text)` → `tokenize(c.indexed_text)`
- [x] 3.5 **`chroma_store.py` 保持 `c.text`（不动）**

## 4. CLI + 测试

- [x] 4.1 `cli.py` ingest：条件构造 contextualizer（`if settings.contextualization_enabled`）+ ContextCache，传入 pipeline
- [x] 4.2 集成测试：CR 开 → child.context 非空 + indexed_text=context+text + embed 用 indexed_text + Chroma 存原文
- [x] 4.3 集成测试：CR 关 → indexed_text=text（行为不变）
- [x] 4.4 全测试 + 覆盖率 ≥ 80%
