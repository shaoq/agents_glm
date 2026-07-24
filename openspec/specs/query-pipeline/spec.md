# query-pipeline Specification

## Purpose
TBD - created by archiving change add-query-pipeline. Update Purpose after archive.
## Requirements
### Requirement: 双路召回 + RRF 融合
系统 SHALL 并行执行向量召回（`VECTOR_TOP_K`）与 BM25 召回（`BM25_TOP_K`），通过 RRF（Reciprocal Rank Fusion，k=60）融合为统一排序；RRF SHALL 只用排名（不用分数），规避向量 distance 与 BM25 score 量纲相反的问题。

#### Scenario: 双路互补召回
- **WHEN** 查询「GLM-4.5 的维度」
- **THEN** 向量路召回语义相关 chunk + BM25 路召回精确词命中 chunk → RRF 融合后两者都排靠前

#### Scenario: 向量 status 过滤
- **WHEN** 向量召回时
- **THEN** `where={"status": "active"}` 过滤 update 中间态旧 chunk

### Requirement: Rerank 精排
系统 SHALL 用智谱 Rerank API（`RERANK_MODEL`）对 RRF 融合后的候选精排，取 `RERANK_TOP_N`（默认 6）；rerank SHALL 在子块文本上做（query + 子块联合评分）。

#### Scenario: 精排缩小候选
- **WHEN** RRF 融合后 20+ 候选
- **THEN** Rerank 精排后保留 top_n=6 个最相关候选

### Requirement: AutoMerging 父子回传
系统 SHALL 在 Rerank 后对 top_n 子块按 `parent_id` 分组：某父块下 ≥ `merge_threshold`（默认 2）个子块命中 → 回传整个父块（`ParentStore.get`）；命中少 → 回传子块本身。

#### Scenario: 多子块命中回传父块
- **WHEN** 同一父块下 2+ 子块在 rerank top_n 中
- **THEN** 回传整个父块（完整上下文），而非多个重叠子块

### Requirement: 上下文构建
系统 SHALL 对检索结果执行：hash 去重 + 父子去重 + token 预算截断（`LLM_MAX_CONTEXT_TOKENS`，tiktoken + buffer）+ 引用编号注入（`[N]（文档名, 页码）文本`）；编号格式 SHALL 由共享常量定义（三方契约）。

#### Scenario: 去重 + 预算 + 编号
- **WHEN** 检索结果含重复 chunk 且总 token 超预算
- **THEN** hash/父子去重后按 rerank 分数降序截断，注入 `[N]（文档名, 页码）` 编号

### Requirement: 生成（GLM-4.5 四约束）
系统 SHALL 用 `LLM_MODEL`（默认 glm-4.5）生成回答，system prompt 包含四约束：① Grounding（仅基于上下文）② 强制引用（论断后标 `[N]`）③ 先结论后展开 ④ 兜底（上下文不足说明「未找到」）；温度 SHALL ≤ 0.3。

#### Scenario: 带引用的回答
- **WHEN** 上下文含 GLM-4.5 维度信息
- **THEN** 生成「GLM-4.5 支持 256-2048 维[1]…」式带引用标注的回答

### Requirement: 引用校验（CitationChecker）
系统 SHALL 对生成的回答后处理校验：正则提取 `[N]` → 与上下文编号集合比对 → 无效引用剔除/标记；SHALL 从检索结果构造 `Citation`。faithfulness_enabled 时 SHALL 执行 faithfulness 二次校验。

**当 `confidence_enabled=True` 时**，系统 SHALL 在 faithfulness 校验后聚合三信号计算 `confidence` 分数：rerank 分数均值（归一化）+ citation 通过率（`len(used_context_ids) / len(id_map)`）+ faithfulness 分数（如可用）。confidence < 阈值时 SHALL 标记 `AnswerStatus.LOW_CONFIDENCE`（展示回答 + ⚠标注「仅供参考」）。`Answer.confidence` SHALL 携带聚合分数。

#### Scenario: 无效引用剔除
- **WHEN** 生成回答含 `[9]` 但上下文只有 [1]-[6]
- **THEN** `[9]` 被标记为无效引用并剔除

#### Scenario: 引用溯源
- **WHEN** 回答含有效 `[1]`
- **THEN** Citation 包含 doc_id / 文档名 / 页码 / 原文片段

#### Scenario: faithfulness 校验打分
- **WHEN** `faithfulness_enabled=True` 且回答有 5 句，其中 4 句被上下文支撑
- **THEN** `Answer.faithfulness_score = 0.8`

#### Scenario: faithfulness 关闭时 score 为 None
- **WHEN** `faithfulness_enabled=False`
- **THEN** `Answer.faithfulness_score = None`

#### Scenario: confidence 高 → ANSWERED
- **WHEN** `confidence_enabled=True` 且三信号聚合 confidence=0.85 ≥ 阈值 0.5
- **THEN** `Answer.status = ANSWERED`，`Answer.confidence = 0.85`

#### Scenario: confidence 低 → LOW_CONFIDENCE（展示+标注）
- **WHEN** `confidence_enabled=True` 且三信号聚合 confidence=0.3 < 阈值 0.5
- **THEN** `Answer.status = LOW_CONFIDENCE`，回答文本仍展示 + ⚠标注

#### Scenario: confidence 关闭 → 不聚合
- **WHEN** `confidence_enabled=False`
- **THEN** `Answer.confidence = None`，status 由 citation/faithfulness 决定（行为不变）

### Requirement: 检索空兜底
系统 SHALL 在 RRF 融合后无结果时直接返回 `status=NO_RESULT`（不调用 Rerank/Generator），附 message 引导用户。

#### Scenario: 检索为空不生成
- **WHEN** 向量+BM25 召回均为空
- **THEN** 返回 Answer(status=NO_RESULT, message="未找到相关内容")，不调用 Rerank/Generator

### Requirement: 查询 CLI
系统 SHALL 提供 `agents-rag ask <question>` 命令，消费已建索引，输出带引用的回答 + 引用来源列表。

#### Scenario: ask 跑通
- **WHEN** 运行 `agents-rag ask "GLM-4.5 支持多少维 embedding？"`（已 ingest）
- **THEN** 返回带 `[N]` 引用标注的回答 + 文档名/页码引用列表

