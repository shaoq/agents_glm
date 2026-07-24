## MODIFIED Requirements

### Requirement: 双路召回 + RRF 融合
系统 SHALL 并行执行向量召回（`VECTOR_TOP_K`）与 BM25 召回（`BM25_TOP_K`），通过 RRF（Reciprocal Rank Fusion，k=60）融合为统一排序；RRF SHALL 只用排名（不用分数），规避向量 distance 与 BM25 score 量纲相反的问题。

**当 `query_rewrite_enabled=True` 时**，系统 SHALL 在检索前用 `QUERY_REWRITE_MODEL`（默认 GLM-4.7-Flash）将原始 query 改写为检索友好（去口语化 + 补术语 + 保留原意，不编答案），并对原 query 与改写 query 各执行一次完整双路召回 + RRF 融合，再用 RRF 融合两组结果。改写失败（异常）或改写结果与原 query 一致时，SHALL 回退为原 query 单路召回 + RRF（不阻塞查询）。

#### Scenario: 双路互补召回
- **WHEN** 查询「GLM-4.5 的维度」
- **THEN** 向量路召回语义相关 chunk + BM25 路召回精确词命中 chunk → RRF 融合后两者都排靠前

#### Scenario: 向量 status 过滤
- **WHEN** 向量召回时
- **THEN** `where={"status": "active"}` 过滤 update 中间态旧 chunk

#### Scenario: 改写开启 → 双 query 融合
- **WHEN** `query_rewrite_enabled=True` 且原 query「密码咋办」被改写为「账户密码重置流程」
- **THEN** 对原 query 与改写 query 各执行一次双路召回 + RRF 融合，再对两组结果做一次 RRF 融合

#### Scenario: 改写失败 → 回退原 query 不阻塞
- **WHEN** `query_rewrite_enabled=True` 但改写 LLM 异常或失败
- **THEN** 回退为原 query 单路双路召回 + RRF，查询正常完成（仅缺失改写增益）

#### Scenario: 改写结果与原 query 一致 → 走原路省检索
- **WHEN** `query_rewrite_enabled=True` 但 LLM 判定原 query 已规范（改写结果与原 query 一致）
- **THEN** 仅对原 query 执行一次双路召回 + RRF（不重复检索）

#### Scenario: 改写关闭 → 行为不变
- **WHEN** `query_rewrite_enabled=False`
- **THEN** 直接对原 query 双路召回 + RRF（行为与基线完全一致）
