## Why

查询管线基线 query 直接 embed 检索（`QueryPipeline.ask` 第①步 `embed(query)`），但原始 query 常不是最佳检索 query（笔记 §2.1）：口语化（"密码咋办"）、表述差异（问"重置密码"文档写"忘记密码处理"→ BM25 全漏）、术语不齐 → recall 打折。查询改写是查询管线最大 recall 杠杆（笔记 §2）。

本提案落地首版改写**原型**，默认关（opt-in），定位为 **eval 实验组**——为后续 recall@k 评测集备好"有改写 vs 无改写"对照（config 开关零成本 A/B），收益由数据驱动决定去留，规避"无 eval 就上改写"的玄学（笔记 §2.5B / §2.6）。

## What Changes

- 新增 `QueryRewriter`（`retrieval/query_rewriter.py`）：用便宜 LLM（默认 GLM-4.7-Flash）把口语/模糊 query 改写为检索友好——去口语化 + 补术语 + 保留原意，**不编答案**（区别于 HyDE，规避领域术语偏差致命点）。结构照搬 `citation/faithfulness.py`（OpenAI client + tenacity retry + `_NonRetryable`）。返回 `str | None`。
- **双 query RRF 融合**：原 query + 改写 query 各跑一次完整 `HybridRetriever`（vector+BM25+RRF），再用现有 `rrf_fuse`（`retrieval/hybrid.py`）融合两组结果。复用现有融合逻辑，不新写。
- **触发判据靠双 query RRF 绕过**：不判断要不要改写，opt-in 开启后全改写；改写无用时 RRF 自动稀释其排名，不伤害结果（规避笔记 §2.5A 最难点）。
- **失败兜底**：改写异常/失败返回 `None`，或 `rewritten == query`（LLM 判定已规范）→ 回退原 query 单路 `HybridRetriever`，**在线查询不因改写失败而失败**。
- **插入点**：`QueryPipeline.ask` 第①步（embed 前）。
- **配置**：`query_rewrite_enabled`(默认 False) + `query_rewrite_model`(默认 GLM-4.7-Flash)；`config.py` + `.env` + `.env.example` 三处同步。
- **改写 prompt** 对齐 `generation/prompts.py` 风格，5 条规则：去口语化 / 补术语 / 保留原意不发散不答 / 简洁关键词式 / 已规范不改。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `query-pipeline`：检索 requirement 增加可选查询改写 + 双 query RRF 融合（opt-in，默认关，关闭时行为完全不变）

## Impact

- **新增**：`retrieval/query_rewriter.py`（`QueryRewriter` + 改写 prompt）
- **改动**：`pipeline/query.py`（`__init__` 加 `rewriter` 参数；`ask()` 第①步双 query 融合，复用 `rrf_fuse`）、`config.py`（2 参数）、`cli.py`（ask 条件注入 rewriter）、`.env` + `.env.example`（2 配置项）
- **测试**：`tests/unit/`（QueryRewriter 单测 + 双 query 融合单测）、`tests/integration/`（pipeline 集成：改写开 / 关 / 失败回退）
- **不动**：`reranker.py` / `context_builder.py` / `generator` / `chroma_store.py` / `ingest.py` / 整个索引侧
- **依赖**：无新增（复用 `openai`、`tenacity`）
- **行为**：默认关，现有行为完全不变；opt-in 启用
