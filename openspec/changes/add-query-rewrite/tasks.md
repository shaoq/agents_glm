# Implementation Tasks

> 查询改写原型：QueryRewriter（Flash 改写，不编答案）+ 双 query RRF 融合（复用 rrf_fuse）。
> 默认关（opt-in），定位 eval 实验组。改写失败/rewritten==query 回退原 query 单路（不阻塞）。

## 1. config + .env

- [x] 1.1 `config.py`：`Settings` 加 `query_rewrite_enabled`(False) + `query_rewrite_model`("GLM-4.7-Flash")
- [x] 1.2 `.env` + `.env.example`：加 `QUERY_REWRITE_ENABLED` / `QUERY_REWRITE_MODEL`（三处键集同步，遵循 CLAUDE.md 规则）

## 2. QueryRewriter

- [x] 2.1 `retrieval/query_rewriter.py`：`QueryRewriter` 类（结构照搬 `citation/faithfulness.py`：OpenAI client + tenacity `@retry` + `_NonRetryable`，复用 `indexing/embedder._NonRetryable/_is_non_retryable`）
- [x] 2.2 改写 prompt（对齐 `generation/prompts.py` 风格，5 规则：去口语化 / 补术语 / 保留原意不发散不答 / 简洁关键词式 / 已规范不改）
- [x] 2.3 `rewrite(query) -> str | None`：返回改写 query；异常/失败返回 `None`（兜底）
- [x] 2.4 单测：改写产出 / retry 重试 / 异常→`None` / 已规范不改（FakeOpenAI，不依赖密钥）

## 3. pipeline 双 query 融合

- [x] 3.1 `pipeline/query.py`：`__init__` 加 `rewriter: QueryRewriter | None = None` 参数
- [x] 3.2 `ask()` 第①步：`rewriter.rewrite(query)` → 有改写则原 query + 改写 query 各跑一次 `HybridRetriever.retrieve`，用 `rrf_fuse` 融合两组结果；`None` / `rewritten == query` → 回退原 query 单路检索
- [x] 3.3 单测：双 query 融合路径（复用 `rrf_fuse`，断言两组都参与）/ 回退路径（改写 `None`、`rewritten == query`）

## 4. CLI + 集成

- [x] 4.1 `cli.py` ask：条件注入 rewriter（`QueryRewriter(...) if settings.query_rewrite_enabled else None`）
- [x] 4.2 集成测试：改写开 → 双 query 融合路径；改写关 → 原路径（行为不变）；改写失败 → 回退不崩（Fake 组件）
- [x] 4.3 全测试 + 覆盖率 ≥ 80%（实测 118 passed，整体 90%，query_rewriter 100%）
- [ ] 4.4（可选）真实密钥端到端：`agents-rag ask` 对比 `QUERY_REWRITE_ENABLED` 开关前后
