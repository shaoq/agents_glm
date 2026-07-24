## Why

当前检索空才拒答（NO_RESULT），但「检索有结果但质量低」时仍硬返回——可能错答（企业场景：错答比不答代价高）。已有三个信号（rerank 分数、citation 通过率、faithfulness 分数），但各自独立、未聚合。需多信号加权 → 置信度分数 → 低置信标注，让用户/系统知道「这个回答可不可信」。

## What Changes

- 查询管线第 ⑩ 步（faithfulness 后）：聚合三信号 → confidence 分数
  - A. rerank 分数均值（`id_map` 中 RetrievalResult.score 归一化）
  - B. citation 通过率（`len(used_context_ids) / len(id_map)`）
  - C. faithfulness 分数（`Answer.faithfulness_score`，可能 None）
- confidence < 阈值 → `AnswerStatus.LOW_CONFIDENCE`（展示回答 + ⚠标注「仅供参考」）
- confidence ≥ 阈值 → `ANSWERED`（正常）
- `AnswerStatus` 加 `LOW_CONFIDENCE` 三态
- `Answer` 加 `confidence: float | None`
- config 加 `confidence_enabled`(默认关) + `confidence_threshold` + 三权重
- 默认关（opt-in）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `query-pipeline`：引用校验 requirement 加置信度聚合拒答（三信号加权 → confidence → answered/low_confidence）

## Impact

- **改动**：`models.py`（AnswerStatus + Answer）、`pipeline/query.py`（第⑩步聚合）、`config.py` + `.env.example`（confidence 参数）、`cli.py`（三态展示）
- **不动**：`retrieval/` / `generation/` / `citation/` / `chroma_store.py` / `ingest.py`
