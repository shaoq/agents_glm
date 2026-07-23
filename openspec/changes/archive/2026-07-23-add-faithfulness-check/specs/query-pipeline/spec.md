## MODIFIED Requirements

### Requirement: 引用校验（CitationChecker）
系统 SHALL 对生成的回答后处理校验：正则提取 `[N]` → 与上下文编号集合比对 → 无效引用（不存在的编号）剔除/标记；SHALL 从检索结果 metadata 构造 `Citation`（doc_id / source_name / page / snippet）。

**当 `faithfulness_enabled=True` 时**，系统 SHALL 在 CitationChecker 之后执行 faithfulness 二次校验：LLM judge 逐句判断回答每句是否被上下文支撑，返回 `faithfulness_score`（忠实句 / 总句），写入 `Answer.faithfulness_score`。faithfulness 校验 SHALL 只打分不拦截（低分回答仍返回，带 score 供展示/后续置信度拒答用）。JSON 解析失败时 SHALL 返回 `faithfulness_score=None`（不阻塞）。

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
- **THEN** `Answer.faithfulness_score = None`（未校验）

#### Scenario: JSON 解析失败不阻塞
- **WHEN** LLM judge 输出非合法 JSON
- **THEN** `faithfulness_score = None`，回答正常返回
