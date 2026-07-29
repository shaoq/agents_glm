## Context

Human Gate 的**写入侧完整**（开 gate + 建 continuation + version 绑定 + actor/role/schema 校验 + Request ID 去重 + 单次消费 + expiry），但**消费侧两环没接**：

**gap 1（apply continuation 未调用）**：`apply_gate_continuation`（coordination.py:332）实现了「按 gate outcome 查 `GateContinuation.next_state_by_outcome` 转 Run state」，含 stale 版本保护（:349-352）+ 未知 outcome 不推进（:354）+ 非法转换不推进（:359-362），有 3 个单元测试。但 grep + GitNexus 确认**调用点只有 tests/**——生产 `GateService.consume`（gates.py:126）/ `service.respond_gate`（service.py:306）都不调。`coordinator._open_gate`（:327）正确建了 continuation 存进 gate，但消费时没人 apply。结果：消费只转 gate 状态（CONSUMED），Run.state 不按 outcome 转。

**gap 2（response 不流回 phase）**：`GateService.respond` 把用户 payload 存进 `gate.response_payload`，但 `LLMGoalNormalizer.normalize(raw_goal, run_id)`（llm_ports.py:137）只用 `raw_goal`，不读 gate。结果：GOAL_CLARIFICATION 消费后重新 normalize，澄清丢失，仍判歧义 → 循环 BLOCKED。

**当前链路**：消费后 gate.is_open=False → advance 不 BLOCKED → 同 phase 再 execute → normalizer 用原 raw_goal → 大概率又 BLOCKED。

## Goals / Non-Goals

**Goals:**

- gap 1：消费时按 outcome apply continuation 转 Run state（4 类 gate 全支持）。
- gap 2：gate response 的业务内容流回 phase（重新 execute 能读到澄清/审批意见）。
- outcome 提取确定性。

**Non-Goals:**

- 不改 Gate 的 version-bound / single-use / at-most-once / expiry 纪律。
- 不改 `coordinator._open_gate`（开 gate + 建 continuation 已正确）。
- 不引入 LLM 猜 outcome（必须显式确定性）。
- 不做 gate 的多轮交互（首期单次响应即消费）。

## Decisions

### 决策 1：outcome 提取——payload 含显式 `outcome` 字段（推荐）

gate response payload 结构：
```json
{"outcome": "clarified", "clarification": "聚焦 2026 年 Agent 框架"}
```

- `outcome` 是显式字符串，必须在 `GATE_CONTINUATION_NEXT[gate_type]` 的 keys 里（如 GOAL_CLARIFICATION 的 `clarified`/`cancelled`）
- `GateService.respond` 校验 `outcome` 合法（非法 → `GateResponseError`）
- 其余字段（`clarification` 等）是业务内容，流回 phase（见决策 2）

**Rationale**：显式字段最确定性，不靠 schema 推断 / role 猜。4 类 gate 的 outcome 词汇固定（`clarified`/`cancelled`/`approved`/`rejected`/`resolved`/`escalated`/`changes`），易校验。

**Alternatives**：按 payload schema 推断 outcome（脆弱）；按 role 推断（role 不携带业务语义）。

### 决策 2：response 流回 phase——方案 B（respond 时 amend Run 目标信息）

**方案 B（推荐）**：`respond_gate` 消费时，把 gate response 的业务内容（澄清）**amend 进 Run 的目标信息**，使重新 normalize 时自然读到。

- GOAL_CLARIFICATION：澄清并入 `Run`（amend `raw_goal` 或新增 `clarified_goal` 字段），normalizer 重新 normalize 读到合并后的目标
- 这样 **normalizer 不直接耦合 gate**（它只读 Run 的目标信息，不知道 gate 存在）

**Rationale**：解耦——phase handler 保持「只读 Run + 调 provider」，不需要懂 gate。gate 是控制流（开/消费/转 state），amend 是把 gate 的业务产物并入 Run 的领域数据。

**Alternatives**：
- **A**：normalizer 直接读 `gate.response_payload`（耦合 gate；normalizer 要懂 gate 类型 + payload 结构；破坏 phase handler 纯粹性）
- **C**：gate response 作为 `PhaseContext` 额外输入（要改 PhaseContext + coordinator 传；改动面大）

**amend 目标**（B 的细节，Open Question）：
- 选项 B1：amend `Run.raw_goal`（澄清并入；但 raw_goal 是原始输入，改它语义模糊）
- 选项 B2（倾向）：新增 `Run.clarified_goal` 字段，normalizer 读 `clarified_goal or raw_goal`（保留原始 + 叠加澄清）

### 决策 3：调用点——`service.respond_gate`（Application 层）

**推荐**：在 `service.respond_gate`（service.py:306）调 `apply_gate_continuation`，而非 `GateService.consume`。

- `respond_gate` 已持有 uow、能 `get(run)`、能从 payload 提取 outcome、能 amend goal
- `GateService.consume` 保持**纯 gate 状态转换**（转 CONSUMED + Event），不依赖 coordination/run
- 分层清晰：GateService 管 gate 生命周期，Application 层管「消费 + 转 state + amend」

**Rationale**：避免 GateService（orchestration）反向依赖 coordination 的 apply 逻辑 + run 读写；Application 层本来就是编排这些的。

**Alternatives**：`GateService.consume` 内调（要扩签名传 run/outcome，且 GateService 职责扩大）。

## Risks / Trade-offs

- **[amend raw_goal vs 新字段]** → 倾向 B2（新字段 `clarified_goal`），保留原始 raw_goal 不变（审计/可追溯）。Open Question 最终定。
- **[4 类 gate 的 response 业务内容差异]** → GOAL_CLARIFICATION 是澄清文本；PLAN_APPROVAL 是批准/拒绝；CONFLICT_RESOLUTION 是冲突处理；FINAL_REVIEW 是审查意见。B 方案要为每类 gate 定义「业务内容 amend 到哪」。首期聚焦 GOAL_CLARIFICATION（最典型），其余按需。
- **[stale continuation 已保护]** → apply_gate_continuation 已查 bound_state_version/plan_version（:349-352），stale 时不转——保留，不重复实现。
- **[outcome 非法]** → respond 校验 outcome 在 GATE_CONTINUATION_NEXT keys 里，非法直接 GateResponseError（不消费）。

## Migration Plan

本地开发、无远端。改动集中在 `service.respond_gate`（加 apply continuation + amend）+ `GateService.respond`（校验 outcome）+ normalizer 读 clarified_goal（决策 2 的 B2）。既有 gate 测试（test_gates / test_gate_continuation）应不回归（apply_gate_continuation 本来就有测试，只是从 test-only 变生产调用）。

## Open Questions

- **amend 目标字段**：B1（改 raw_goal）vs B2（新 `clarified_goal`）？倾向 B2（保留原始）。
- **非 GOAL_CLARIFICATION gate 的业务内容 amend**：PLAN_APPROVAL/CONFLICT_RESOLUTION/FINAL_REVIEW 的 response 业务内容是否也要流回 phase？首期可能只 GOAL_CLARIFICATION 需要（其他 gate 主要靠 outcome 转 state，业务内容次要）。
- **consume 后谁来 drive**：`respond_gate` 消费后 Run 在新 state，但 advance 不自动跑。是否 `respond_gate` 内调 `drive_run`？还是要求用户再 `run watch`？（当前 respond_gate 不 drive，保持显式）
