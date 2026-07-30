## Context

当前 `RunCoordinator._open_gate` 会把 `GateContinuation` 持久化到 Gate，但生产消费路径 `OrchestrationService.respond_gate` 只调用 `GateService.respond` 和 `GateService.consume`。`apply_gate_continuation` 仅在测试中被直接调用，Run 不会按 outcome 推进。

GOAL_CLARIFICATION 还有第二个缺口：`GoalPhaseHandler` 始终把 `run.raw_goal` 传给 normalizer，Gate 中的 `response_payload` 不会成为下一次 phase 输入。由于 Gate 打开时 Run 保持 `NORMALIZING`，`clarified -> NORMALIZING` 又是 same-state continuation，仅调用现有 `apply_gate_continuation` 不会递增版本，也无法形成新的 phase 输入 generation。

现有 Gate response 校验也是部分实现：payload 是自由 `dict`，`allowed_response_schema` 只持久化而未执行。此 change 因而不能继续假设“schema 校验已完整”，必须先把与消费相关的 outcome 和业务字段变成确定性合约。

## Goals / Non-Goals

**Goals:**

- 让 4 类 Gate 的 8 个 outcome 都通过持久化 continuation 确定性解析目标状态。
- 在一个事务内完成 Gate 响应/消费、Run amendment/转换、CAS 保存和 durable events。
- 让 same-state resume 形成新的 `state_version`，避免复用旧 phase fingerprint。
- 让 GOAL_CLARIFICATION 的补充说明与原始目标组合后重新 normalize。
- 对 stale continuation、非法 payload、CAS 冲突和终态 outcome 给出明确、可测试的行为。

**Non-Goals:**

- 不在 `respond_gate` 中自动调用 `drive_run`；消费与执行推进保持两个显式步骤。
- 不实现 Gate 多轮对话；一次合法响应即消费。
- 首期只有 GOAL_CLARIFICATION 的业务内容成为 phase 输入；其他 Gate 的 feedback/resolution/comment 做类型校验并持久化，但不改 Planner/Research/Review provider 接口。
- 不新增 FINAL_REVIEW 的开 Gate 策略；只保证该类型一旦存在即可正确消费。
- 不在本 change 中重构 actor/scope 的领域含义或补齐既有 Human Gate capability 的全部授权遗留项。

## Decisions

### 决策 1：使用按 Gate 类型和 outcome 区分的类型化响应

新增类型化 Gate response 模型，并由一个确定性 validator 根据 `gate.gate_type` 解析 payload，返回规范化的 `outcome` 和业务字段。禁止缺失 outcome、未知 outcome、错误类型和未声明字段。

最低业务字段如下：

| Gate | Outcome | 必填业务字段 |
|---|---|---|
| GOAL_CLARIFICATION | `clarified` | 非空 `clarification` |
| GOAL_CLARIFICATION | `cancelled` | 无；可选 `reason` |
| PLAN_APPROVAL | `approved` | 无；可选 `comment` |
| PLAN_APPROVAL | `rejected` | 非空 `feedback` |
| CONFLICT_RESOLUTION | `resolved` | 非空 `resolution` |
| CONFLICT_RESOLUTION | `escalated` | 非空 `reason` |
| FINAL_REVIEW | `approved` | 无；可选 `comment` |
| FINAL_REVIEW | `changes` | 非空 `feedback` |

开 Gate 时把对应模型生成的 canonical JSON Schema 写入 `allowed_response_schema`，作为可展示、可审计的 allowed response；运行时以同一类型模型校验，避免解析任意 schema 字符串和引入新的 JSON Schema 依赖。

**Rationale**：仅校验 outcome 会允许 `{outcome: "clarified"}` 被消费，随后因为缺少 clarification 再次循环。类型化判别联合能同时约束 outcome 和业务内容。

**Alternatives**：

- 只校验 `GATE_CONTINUATION_NEXT` 的 key：无法保证业务字段存在。
- 运行时解释任意 JSON Schema：扩大依赖和攻击面，且当前 Gate 类型集合固定。

### 决策 2：continuation resolution 必须返回显式状态

把 continuation 判定与 Run mutation 分离。新增纯解析结果，至少包含：

- `APPLIED`：目标状态与当前状态不同且转换合法；
- `SAME_STATE`：目标状态等于当前状态，仍是一次合法 resume；
- `MISSING_CONTINUATION`：Gate 没有持久化 continuation；
- `STALE`：bound state/plan version 与 Run 不符；
- `UNKNOWN_OUTCOME`：持久化 continuation 不含该 outcome；
- `INVALID_TRANSITION`：目标状态违反 Run 状态机。

现有 `apply_gate_continuation` 可委托该 resolver 保持兼容，但 Application 层使用显式结果，不能再靠“返回了同一个 Run 对象”猜测 stale、same-state 或 invalid。

**Rationale**：GOAL clarified、PLAN rejected、FINAL changes 都可能是 same-state。它们必须递增 `state_version` 才能形成新 phase generation，而 stale 绝不能做业务 amendment。

### 决策 3：目标澄清作为上下文叠加，而不是替换 raw_goal

`Run` 新增可选 `goal_clarification: str | None` 和只读 `effective_goal`：

```text
<raw_goal>

User clarification:
<goal_clarification>
```

没有 clarification 时 `effective_goal == raw_goal`。`GoalPhaseHandler.execute` 把 `ctx.run.effective_goal` 传给现有 `GoalNormalizer.normalize(raw_goal, run_id)`；normalizer protocol 和 LLM port 无需了解 Run 或 Gate。

消费 `GOAL_CLARIFICATION/clarified` 时写入 `goal_clarification`。`raw_goal` 永不覆盖，保证审计可追溯。

**Rationale**：把 `clarified_goal or raw_goal` 放进 `LLMGoalNormalizer` 在现有接口上不可行，而且会把补充片段误当完整目标。

### 决策 4：一个事务、一次最终 Run CAS

`respond_gate` 的事务顺序固定为：

1. 加载 Gate 和对应 Run；
2. 校验 Gate 生命周期、角色/既有约束以及类型化 payload；
3. 解析 continuation，确认不是 stale/unknown/invalid；
4. 将 Gate 转为 RESPONDED，再转为 CONSUMED；
5. 从原 Run 构造最终 Run：
   - GOAL clarified 同时写入 `goal_clarification`；
   - PLAN_APPROVAL Gate 打开前持久化已验证的 `PROPOSED` Plan；approved 时在消费事务内接受该 Plan 并物化 Tasks/Dependencies；
   - 非终态 outcome 应用目标状态；
   - same-state 也更新 `updated_at` 并把 `state_version` 加一；
   - cancelled 使用 `TerminationReason.CANCELED`；
   - escalated 使用 `TerminationReason.FAILED`；
6. 只调用一次 `uow.runs.save(final_run, expected_version=original.state_version)`；
7. 追加 Gate 和 Run events 后提交。

任一步失败都回滚 Gate、Run、dedup claim 和 events。禁止先保存 amendment、再从旧 Run 保存 moved Run，避免字段丢失或自行制造 stale。

### 决策 5：无法安全应用的 Gate 失效，不消费、不修改 Run

当 resolution 为 `MISSING_CONTINUATION`、`STALE`、`UNKNOWN_OUTCOME` 或 `INVALID_TRANSITION` 时，该响应不能作用于当前 Run。系统在独立、可提交的失效路径中把 Gate 标记为 CANCELED，记录新的 `GATE_INVALIDATED` 事件及具体 reason，然后向调用者返回稳定的 Gate continuation error。Run 及其目标信息保持不变，失效 Gate 不再阻塞执行。

**Rationale**：保持此类 Gate OPEN 会永久阻塞 Run；把无法安全应用的响应当作正常 CONSUMED 又会错误表示用户决定已作用于当前版本。

### 决策 6：消费成功写入 durable resume 语义，但不自动 drive

每次合法消费写入 `RUN_RESUMED`，payload 至少包含 `gate_id`、`gate_type`、`outcome`、原/新 state version：

- 状态发生变化时同时写 `RUN_STATE_TRANSITION`；
- 进入 CANCELED/FAILED 时写 `RUN_TERMINATED`，并带 termination reason；
- Gate 仍写 `GATE_RESPONDED` 和 `GATE_CONSUMED`。
- stale Gate 不写正常响应/消费事件，只写 `GATE_INVALIDATED`。
- Gate 后发事件使用事件发生时的当前或最终 Run state version，使 `after_state_version` 增量消费者不会遗漏这些事件。

所有事件与 Gate/Run 状态在同一事务提交。后续 `advance_run`、`drive_run` 或 watch 再创建新的 Attempt/Lease；`respond_gate` 保持同步、有限、可重试。

## Risks / Trade-offs

- **[BREAKING payload contract]** → 更新 CLI/Python 示例、测试 fixture 和所有直接 `GateService.respond` 调用；非法旧 payload 返回稳定的 `GateResponseError`。
- **[same-state version 增长]** → 这是有意的 semantic generation bump，用于使 phase fingerprint 和旧 observation 失效。
- **[只回流 Goal 业务内容]** → 其他 Gate 的内容先类型化持久化；若后续需要让 rejection feedback 驱动 Planner/Reviewer，应另建 change 修改 provider contract。
- **[Gate 失效需要提交后报错]** → 将“失效 Gate”与“正常响应事务”分成清晰分支，避免异常触发 Unit of Work 自动回滚失效记录。
- **[旧持久化 Run]** → 新字段为可选且有默认值，无 SQLite schema migration；回滚版本应忽略 JSON 中未知字段或先确认 Pydantic 兼容策略。

## Migration Plan

1. 先增加可选 Run 字段、类型化 response 模型和 continuation resolver，保持旧读取兼容。
2. 更新 Gate open 路径生成 canonical allowed response schema。
3. 切换 `respond_gate` 到原子消费算法，并同时迁移内部测试 payload。
4. 更新 CLI/Python API 文档和调用示例，明确这是 payload 合约的破坏性变化。
5. 运行 Gate 单元、集成、E2E、完整测试和覆盖率检查；若需要回滚，恢复旧 respond 路径，新 Run 字段不会要求数据库降级。

## Open Questions

无。本 change 的实现决策已固定；其他 Gate 业务内容是否进入 provider context 作为后续独立 change 评估。
