# Implementation Tasks

> Faithfulness 二次校验：CitationChecker 后，LLM judge 逐句判断答案是否忠于上下文。
> 默认关（opt-in），只打分不拦截。笔记 §7.4。

## 1. models + config

- [x] 1.1 `models.py`：Answer 加 `faithfulness_score: float | None = None`
- [x] 1.2 `config.py` + `.env.example`：加 `faithfulness_enabled`(False) / `faithfulness_model`(GLM-4.7-Flash)

## 2. FaithfulnessChecker（新模块）

- [x] 2.1 `citation/faithfulness.py`：`FaithfulnessChecker.check(answer_text, context_str) → float | None`
  - LLM judge prompt：传入 answer + context → 逐句判断 supported → 输出 JSON `[{sentence, supported}]`
  - JSON 解析 → 分数 = supported 句 / 总句
  - 解析失败返回 None（兜底）
  - 复用 tenacity + `_NonRetryable`（照搬 OpenAIGenerator 模式）
- [x] 2.2 单测：Fake LLM → JSON 解析 + 分数计算 + 解析失败兜底

## 3. pipeline + CLI

- [x] 3.1 `pipeline/query.py`：`__init__` 加 `faithfulness_checker` 可选参数；`ask()` 第 8 步后插 `if self.faithfulness_checker: score = check(answer.text, context_str); answer = model_copy(update={"faithfulness_score": score})`
- [x] 3.2 `cli.py` ask 命令：条件构造 FaithfulnessChecker（`if settings.faithfulness_enabled`），传入 pipeline；输出 faithfulness_score（如非 None）
- [x] 3.3 集成测试：faithfulness 开 → score 非空；关 → score=None

## 4. 全测试

- [x] 4.1 全测试 + 覆盖率 ≥ 80%
