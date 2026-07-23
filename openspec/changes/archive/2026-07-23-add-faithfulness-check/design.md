## Context

笔记 §7.4 深入讨论了 faithfulness 与 CitationChecker 的互补关系。CitationChecker 管编号格式（免费），faithfulness 管内容忠实（需 LLM judge）。两者不替代，互补。

## Goals / Non-Goals

**Goals:**
- 生成回答后 LLM judge 逐句校验 → faithfulness 分数（忠实句/总句）
- Answer 携带 faithfulness_score（供展示/后续置信度聚合）
- 默认关（opt-in，有 LLM 成本）

**Non-Goals:**
- 置信度聚合拒答（多信号加权 → 阈值 → 拒答，后续做）
- 内容一致性校验（NLI，编号对但数值错——faithfulness 部分覆盖）
- Self-Check 修正循环（生成→校验→反馈修正，后续做）
- RAGAS faithfulness（需 ragas 依赖，评测专项）

## Decisions

**1. 插入点：CitationChecker 后（第 9 步）。**
```
⑦ 生成 → ⑧ CitationChecker（编号校验）→ ⑨ FaithfulnessChecker（内容校验）→ Answer
```
CitationChecker 先剔除无效引用 → faithfulness 校验清洗后的回答。

**2. LLM judge prompt + JSON 输出。**
prompt 传入 answer + context → LLM 逐句分解 → 输出 `[{sentence, supported: bool}]` JSON → 解析算分。避免自由文本解析。

**3. judge 模型用 Flash（便宜）。**
避免用主生成模型（GLM-5.2）自判——「自己判自己」过度自信。Flash 便宜且 judge 任务不需要强生成能力。

**4. 只打分不拦截。**
faithfulness 低分 → Answer 仍返回（带 score），不拦截。拦截留给后续置信度聚合拒答（多信号综合判断，不单凭 faithfulness 拒）。

**5. 复用 retry 客户端模式。**
FaithfulnessChecker 照搬 OpenAIGenerator / OpenAIContextualizer 的 tenacity + _NonRetryable 模式。

**6. 默认关（opt-in）。**
`faithfulness_enabled=False`。每查询 1 次 LLM judge（成本/延迟），不是所有场景需要。

## Risks / Trade-offs

- **[judge 偏差]** → LLM judge 漏判（认为有支撑但实际编造）；缓解：强模型 judge + 后续可多次采样取多数
- **[成本/延迟]** → 每查询 +1 次 LLM；默认关（opt-in），需时开启
- **[JSON 解析失败]** → LLM 可能不严格输出 JSON；兜底：解析失败返回 faithfulness_score=None（不阻塞回答）
