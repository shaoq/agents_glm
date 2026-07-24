## Context

笔记 §6.4 深入讨论了置信度信号谱（6 方案）+ 盲区 + 聚合方案。本轮用三个可靠信号（rerank+citation+faithfulness）加权聚合。faithfulness_score 已实现（add-faithfulness-check），rerank 分数 + citation 通过率在现有管线中可直接计算。

## Goals / Non-Goals

**Goals:**
- 三信号加权 → confidence 分数
- confidence < 阈值 → LOW_CONFIDENCE（展示回答 + ⚠标注）
- Answer 携带 confidence（供 UI/后续拒答用）

**Non-Goals:**
- no_result 拒答（检索空兜底已覆盖；本轮只做 low_confidence 标注，不丢弃回答）
- 信号权重评测标定（首版用经验值 0.3/0.3/0.4，评测后调）
- logprob/自评/一致性等其他信号（笔记 §6.4 评估为低价值/高成本）

## Decisions

**1. 插入点：第 ⑩ 步（faithfulness 后）。**
所有三个信号在此时都已产出（rerank ④、citation ⑧、faithfulness ⑨）。

**2. 三信号获取（无需改 CitationChecker/Reranker）。**
- A rerank：`mean(r.score for r in id_map.values())` → min-max 归一化到 [0,1]
- B citation：`len(answer.used_context_ids) / len(id_map)`（已有数据，无需改 CitationChecker）
- C faithfulness：`answer.faithfulness_score`（可能 None）

**3. faithfulness None 的处理。**
faithfulness 禁用/失败时 None → 从聚合剔除 + 重新归一化权重（A+B 两信号加权，权重按比例放大）。

**4. 聚合公式。**
```python
confidence = (w_rerank * norm_rerank + w_citation * citation_rate + w_faith * faithfulness)
            / (w_rerank + w_citation + w_faith_active)    # w_faith_active=0 when None
```
默认权重：rerank 0.3 / citation 0.3 / faithfulness 0.4（faithfulness 权重最高——最直接反映忠实性）。

**5. 阈值：单一 threshold（首版简化）。**
- confidence ≥ threshold → ANSWERED
- confidence < threshold → LOW_CONFIDENCE（展示 + ⚠标注）
- threshold 默认 0.5（经验值，评测后调）

**6. low_confidence 不丢弃回答。**
与 no_result 不同——low_confidence 仍展示回答文本 + 引用（有价值），只加 ⚠标注。用户自己判断可信度。

**7. 默认关（opt-in）。**
`confidence_enabled=False`。置信度聚合需三信号（faithfulness 也需开启），不是所有场景需要。

## Risks / Trade-offs

- **[rerank 分数语义]** → AutoMerging 的 merged 条目 score=hits[0].score（非平均），可能偏高。首版接受（均值近似够用）
- **[权重未标定]** → 首版 0.3/0.3/0.4 经验值，评测后调
- **[faithfulness None]** → 重新归一化权重处理（不阻塞）
- **[阈值 0.5 通用性]** → 不同场景最优阈值不同，需评测标定
