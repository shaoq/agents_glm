## MODIFIED Requirements

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
