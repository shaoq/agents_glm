## ADDED Requirements

### Requirement: 类型化且确定性的 Gate response

系统 SHALL 根据 Gate 类型和显式 `outcome` 使用类型化 response contract 校验 payload。未知或缺失 outcome、字段类型错误、未声明字段以及 outcome 所需业务字段缺失或为空时，系统 MUST 在响应和消费前拒绝请求，Gate 保持 OPEN，Run 不变。

#### Scenario: GOAL_CLARIFICATION clarified 要求澄清内容

- **WHEN** GOAL_CLARIFICATION response 的 outcome 为 `clarified`
- **THEN** payload MUST 包含非空字符串 `clarification`

#### Scenario: PLAN_APPROVAL rejected 要求反馈

- **WHEN** PLAN_APPROVAL response 的 outcome 为 `rejected`
- **THEN** payload MUST 包含非空字符串 `feedback`

#### Scenario: CONFLICT_RESOLUTION resolved 要求解决方案

- **WHEN** CONFLICT_RESOLUTION response 的 outcome 为 `resolved`
- **THEN** payload MUST 包含非空字符串 `resolution`

#### Scenario: CONFLICT_RESOLUTION escalated 要求原因

- **WHEN** CONFLICT_RESOLUTION response 的 outcome 为 `escalated`
- **THEN** payload MUST 包含非空字符串 `reason`

#### Scenario: FINAL_REVIEW changes 要求修改意见

- **WHEN** FINAL_REVIEW response 的 outcome 为 `changes`
- **THEN** payload MUST 包含非空字符串 `feedback`

#### Scenario: 非法 payload 不产生副作用

- **WHEN** response payload 缺少合法 outcome 或不满足对应类型化 contract
- **THEN** 系统抛出 `GateResponseError`，Gate 保持 OPEN，Run、Request ID dedup 和事件流均不改变

### Requirement: 全部 Gate outcome 通过 continuation 解析

系统 SHALL 只使用 Gate 中持久化的 `GateContinuation.next_state_by_outcome` 解析目标状态，调用者不得直接指定目标。系统 MUST 区分状态改变、same-state、stale、未知 outcome 和非法状态转换。

#### Scenario: GOAL_CLARIFICATION clarified

- **WHEN** 合法 GOAL_CLARIFICATION response 的 outcome 为 `clarified`
- **THEN** Run 的 continuation 目标为 NORMALIZING，并形成新的 state version

#### Scenario: GOAL_CLARIFICATION cancelled

- **WHEN** 合法 GOAL_CLARIFICATION response 的 outcome 为 `cancelled`
- **THEN** Run 进入 CANCELED，termination reason 为 CANCELED

#### Scenario: PLAN_APPROVAL approved

- **WHEN** 合法 PLAN_APPROVAL response 的 outcome 为 `approved`
- **THEN** 系统接受 Gate 打开前持久化的待审批 Plan，在同一事务物化其 Tasks/Dependencies，并让 Run 以该 Plan version 进入 RESEARCHING

#### Scenario: PLAN_APPROVAL rejected

- **WHEN** 合法 PLAN_APPROVAL response 的 outcome 为 `rejected`
- **THEN** Run 回到或保持 PLANNING，并形成新的 state version

#### Scenario: CONFLICT_RESOLUTION resolved

- **WHEN** 合法 CONFLICT_RESOLUTION response 的 outcome 为 `resolved`
- **THEN** Run 进入 RESEARCHING

#### Scenario: CONFLICT_RESOLUTION escalated

- **WHEN** 合法 CONFLICT_RESOLUTION response 的 outcome 为 `escalated`
- **THEN** Run 进入 FAILED，termination reason 为 FAILED

#### Scenario: FINAL_REVIEW approved

- **WHEN** 合法 FINAL_REVIEW response 的 outcome 为 `approved`
- **THEN** Run 进入 FINALIZING

#### Scenario: FINAL_REVIEW changes

- **WHEN** 合法 FINAL_REVIEW response 的 outcome 为 `changes`
- **THEN** Run 回到或保持 REVIEWING，并形成新的 state version

### Requirement: 无法安全应用的 continuation 失效

当 Gate continuation 缺失、绑定版本 stale、不包含已验证 outcome 或目标状态转换非法时，系统 MUST 拒绝把响应应用到 Run，并使该 Gate 失效，避免不可应用的 Gate 继续阻塞执行。

#### Scenario: stale Gate response

- **WHEN** Gate continuation 的 bound state version 或 bound plan version 与当前 Run 不符
- **THEN** Gate 进入 CANCELED 并记录 `GATE_INVALIDATED`，Run 及其目标信息不变，响应返回稳定的 stale error

#### Scenario: continuation 缺失或损坏

- **WHEN** Gate 没有 continuation、continuation 不包含已验证 outcome 或映射到非法状态转换
- **THEN** Gate 进入 CANCELED 并记录带具体 reason 的 `GATE_INVALIDATED`，Run 不变，响应返回稳定的 continuation error

### Requirement: Gate 消费与 Run resume 原子提交

合法 Gate response SHALL 在同一个 Unit of Work 中完成 Gate RESPONDED、Gate CONSUMED、Run amendment/转换、一次基于原 state version 的 CAS 保存以及 durable events。任一步失败 MUST 回滚全部副作用。

#### Scenario: same-state resume 递增版本

- **WHEN** 合法 continuation 的目标状态等于 Run 当前状态
- **THEN** Run 状态保持不变，但 `state_version` 恰好递增一次并更新 `updated_at`

#### Scenario: CAS 冲突

- **WHEN** 最终 Run 保存发生 expected version 冲突
- **THEN** Gate 状态、Run、dedup claim 和事件均保持事务开始前状态

#### Scenario: Run 只保存一次

- **WHEN** 合法响应同时包含 Run amendment 和状态转换
- **THEN** 系统从同一个原始 Run 构造最终 Run，并仅执行一次以原 state version 为 expected version 的 CAS 保存

### Requirement: 目标澄清作为有效目标上下文

GOAL_CLARIFICATION 的 clarification SHALL 作为原始目标的补充上下文持久化到 Run。Goal phase MUST 使用由原始目标和 clarification 组成的 effective goal；原始 `raw_goal` MUST 保持不变。

#### Scenario: 澄清后重新 normalize 成功

- **WHEN** 用户提交合法 `{outcome: "clarified", clarification: "..."}` 并消费 Gate
- **AND** Run 后续执行 NORMALIZING phase
- **THEN** normalizer 接收到同时包含原始目标和用户 clarification 的 effective goal，能够基于新增信息继续归一化

#### Scenario: 原始目标保持可追溯

- **WHEN** clarification 被写入 Run
- **THEN** `raw_goal` 与创建 Run 时完全一致，clarification 存在独立可选字段中

### Requirement: durable resume 和终态事件

每次合法 Gate 消费 SHALL 在同一事务记录 `GATE_RESPONDED`、`GATE_CONSUMED` 和 `RUN_RESUMED`。状态改变时 MUST 记录 `RUN_STATE_TRANSITION`；进入终态时 MUST 记录 `RUN_TERMINATED` 和正式 TerminationReason。

#### Scenario: 合法 Gate 消费事件

- **WHEN** Gate response 被成功消费
- **THEN** durable event 包含 gate ID、Gate 类型、outcome、原 state version 和新 state version

#### Scenario: Gate 后发事件可被增量消费

- **WHEN** 观察者已消费到 Gate 打开时的 state version，随后 Gate 被成功消费或因 stale continuation 失效
- **THEN** 新增的 `GATE_RESPONDED`、`GATE_CONSUMED` 或 `GATE_INVALIDATED` 使用事件发生时的当前或最终 Run state version，并可由 `after_state_version` 查询返回

#### Scenario: 消费后显式推进

- **WHEN** Gate 已成功消费且 Run 已 resume
- **THEN** `respond_gate` 返回而不自动 drive，后续 `advance_run`、`drive_run` 或 watch 创建新的执行 claim
