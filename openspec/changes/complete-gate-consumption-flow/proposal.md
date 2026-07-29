## Why

深度代码分析（main `d7c8d22`）发现 Human Gate 的**消费链路不完整**——核心逻辑 `apply_gate_continuation`（按 gate outcome 转 Run state）**只在测试里被调用，生产代码完全不调**；且 gate response（用户提交的澄清/审批内容）**没有流回 phase**。导致 Gate 消费后 Run 推进不正确，典型表现是 `GOAL_CLARIFICATION` 消费后重新 normalize 仍判歧义、循环 BLOCKED。

这是连续代码阅读发现的**第四处「标记完整、消费待接」**（前三处：StageExecution 复用[伪优化已排除]、`AWAITING_RETRY→READY`、observation 有界放弃；后两者已纳入 `complete-runtime-retry-and-stage-replay` change）。Gate 的写入侧（开 gate + 建 continuation + version 绑定 + 校验 + 单次消费）全部就绪，唯独「消费时 apply continuation 转 state」和「response 流回 phase」两环没接。

## What Changes

- **gap 1 修复（apply continuation）**：在 Gate 消费路径（`GateService.consume` 或 `service.respond_gate`）调用 `apply_gate_continuation(gate, run, outcome, now)`，按 gate response 的 outcome 转 Run state（走 `GATE_CONTINUATION_NEXT` 的映射，4 类 gate 都支持）。
- **outcome 提取**：从 gate response payload 确定性地提取 outcome 字符串（如 `GOAL_CLARIFICATION` 的 `clarified`/`cancelled`）。payload 结构含显式 `outcome` 字段（+ 业务内容如澄清文本）。
- **gap 2 修复（response 流回 phase）**：让 gate response 的业务内容（澄清/审批意见）流回 phase，使重新 execute 时 phase 能读到——避免 normalizer 直接耦合 gate。
- **配套测试**：`GOAL_CLARIFICATION` 消费后 Run 正确转 state + 重新 normalize 用上澄清 → PROGRESSED（不循环）；4 类 gate 的 outcome→state 转换都正确。

## Capabilities

### New Capabilities

- `human-gate-consumption-flow`: Human Gate 消费的完整性——按 outcome apply continuation 转 Run state，并将 gate response 的业务内容流回后续 phase。

### Modified Capabilities

<!-- 现有 openspec/specs/ 下的 capability 均属 RAG/Memory 检索能力，本 change 不改变其 spec 级需求。 -->

## Impact

- **代码**：`orchestration/gates.py`（`GateService.consume` apply continuation）/ `application/service.py`（`respond_gate`）/ `orchestration/coordination.py` 或 `domain/coordination.py`（outcome 提取）；gap 2 视方案涉及 `normalizer` 读澄清 或 `respond_gate` amend goal。
- **既有纪律不变**：Gate 的 version-bound（continuation 绑 `state_version`/`plan_version`，stale 时不转，`apply_gate_continuation` 已实现 :349-352）、single-use（CONSUMED 后不可再 respond）、at-most-once（Request ID 去重）、expiry action 全部保留。
- **outcome 提取确定性**：payload → outcome 必须确定性映射（显式 `outcome` 字段），不靠 LLM 猜。
- **4 类 gate 都支持**：`GOAL_CLARIFICATION` / `PLAN_APPROVAL` / `CONFLICT_RESOLUTION` / `FINAL_REVIEW`。
- **apply 仍走 CAS**（`run.save expected_version`），不绕过版本校验。
