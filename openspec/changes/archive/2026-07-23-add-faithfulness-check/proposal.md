## Why

当前抗幻觉只有两层：① prompt grounding（~80%）+ ② CitationChecker（引用编号校验，格式层面）。但编号命中 ≠ 内容忠实——LLM 可能标了正确的 `[1]`，但论断内容偏离了引用原文（如上下文说 2048 维，生成写 4096 维`[1]`）。faithfulness 补第 3 层：LLM judge 逐句判断答案是否被上下文支撑，检测「编造」。

## What Changes

- 新增 `citation/faithfulness.py`：`FaithfulnessChecker.check(answer_text, context_str) → float`（LLM judge 逐句校验，返回忠实句占比）
- `Answer` 加 `faithfulness_score: float | None = None`
- `pipeline/query.py` 第 8 步（CitationChecker）后插第 9 步（FaithfulnessChecker）
- config 加开关（`faithfulness_enabled=False` 默认关）+ judge 模型
- faithfulness **只打分不拦截**（低分回答仍返回，带 score 供 UI 展示 / 后续置信度拒用）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `query-pipeline`：引用校验加 faithfulness 二次校验（LLM judge 逐句判断内容忠实性，补充 CitationChecker 的编号格式校验）

## Impact

- **新建**：`citation/faithfulness.py`（FaithfulnessChecker）
- **改动**：`models.py`（Answer + faithfulness_score）、`pipeline/query.py`（插入第 9 步）、`config.py` + `.env.example`（faithfulness 参数）、`cli.py`（条件构造）
- **不动**：`retrieval/` / `generation/` / `chroma_store.py` / `ingest.py`
