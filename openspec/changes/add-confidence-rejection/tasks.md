# Implementation Tasks

> 置信度聚合拒答：rerank + citation + faithfulness 三信号加权 → confidence → answered/low_confidence。
> 默认关（opt-in）。low_confidence 展示回答 + ⚠标注（不丢弃）。

## 1. models + config

- [ ] 1.1 `models.py`：AnswerStatus 加 `LOW_CONFIDENCE`；Answer 加 `confidence: float | None = None`
- [ ] 1.2 `config.py` + `.env.example` + `.env`：加 `confidence_enabled`(False) / `confidence_threshold`(0.5) / `confidence_weight_rerank`(0.3) / `confidence_weight_citation`(0.3) / `confidence_weight_faithfulness`(0.4)

## 2. pipeline 聚合

- [ ] 2.1 `pipeline/query.py`：`__init__` 加 confidence 参数（threshold + 三权重）
- [ ] 2.2 `ask()` 第⑩步（faithfulness 后）：聚合三信号 → confidence → model_copy(update={"confidence": c, "status": LOW_CONFIDENCE if c < threshold})
  - rerank: `mean(r.score for r in id_map.values())` 归一化
  - citation: `len(answer.used_context_ids) / len(id_map)`
  - faithfulness: `answer.faithfulness_score`（None 时剔除+重新归一化）
- [ ] 2.3 单测：三信号聚合（高分 ANSWERED / 低分 LOW_CONFIDENCE / faithfulness None 处理）

## 3. CLI + 集成

- [ ] 3.1 `cli.py`：`_print_answer` 加 LOW_CONFIDENCE 三态展示（⚠标注 + 仍渲染回答 + 引用）
- [ ] 3.2 `cli.py` ask：条件传 confidence 参数（`if settings.confidence_enabled`）
- [ ] 3.3 集成测试：confidence 开 + 信号高 → ANSWERED；信号低 → LOW_CONFIDENCE
- [ ] 3.4 全测试 + 覆盖率 ≥ 80%
