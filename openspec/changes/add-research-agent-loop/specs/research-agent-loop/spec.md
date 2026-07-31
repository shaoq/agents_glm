## ADDED Requirements

### Requirement: Plan approves non-empty seeds and an explicit exploration boundary

每个 `agent_loop` Plan SHALL 包含至少一个 `EVIDENCE_RESEARCHER` seed Task，并携带版本化 `ExplorationBoundary`。Boundary MUST 声明 allowed capabilities、按 seed 的 required coverage、每 seed `max_steps`、每 seed `max_directions` 和可选每 seed loop token/cost ceiling；required coverage MUST 是 allowed capabilities 的子集，所有上限 MUST 不超过系统与 Run Policy，有限 Run budget 下所有 seed 最坏 ceiling 总和 MUST 不超过可用 Run budget，PLAN_APPROVAL MUST 展示并绑定这些字段。

#### Scenario: Agent-loop Plan 被接受

- **WHEN** Planner 提交包含非空 seed Tasks、`research_execution_mode=agent_loop` 和合法 ExplorationBoundary 的 Plan Proposal
- **THEN** PlanValidator 接受其 seed、coverage、capability、step、direction 和 budget 上限
- **AND** materialized Tasks 与 Boundary 绑定同一 Plan version

#### Scenario: 空 seed 被拒绝

- **WHEN** agent-loop Plan 不包含 seed Task
- **THEN** PlanValidator 在任何 Plan、Task、Run、Gate 或事件写入前拒绝该 Proposal

#### Scenario: Boundary 试图扩大系统策略

- **WHEN** ExplorationBoundary 包含未允许 capability，或 max_steps/max_directions/budget ceiling 超过有效系统或 Run Policy
- **THEN** PlanValidator 拒绝 Proposal，Agent 无法通过文本输出覆盖该结果

### Requirement: Research Agent 每步只能提出一个强类型动作

ResearchAgent SHALL 从当前 objective、seed、accepted Evidence digest、Direction、coverage 和剩余边界构造的只读 LoopView 中，每步恰好提出一个 `QUERY`、`ADD_DIRECTION` 或 `STOP_REQUEST`。模型输出 MUST NOT 直接提交 Run/Task/Plan 状态、capability ID、request ID、预算或持久化写入；ActionValidator SHALL 在执行前验证所有正式约束。

#### Scenario: QUERY 被接受

- **WHEN** Agent 为当前 Direction 提出位于 Boundary 内的 capability kind 和限长 query
- **THEN** Runtime 构造稳定 request ID，并通过 CapabilityRouter 执行至多一次 capability 调用

#### Scenario: 非法 action 被拒绝

- **WHEN** action schema 无效、引用未知 Direction、选择 Boundary 外 capability、越过 step/direction/budget 上限或携带正式状态 mutation
- **THEN** ActionValidator 将当前 step 归类为 `INVALID_RESPONSE` 或 `POLICY_VIOLATION`
- **AND** 不创建 Evidence、Direction、Plan version、Run transition 或 budget consumption

### Requirement: 新方向是 plan-scoped loop-local 状态

`ADD_DIRECTION` SHALL 使用共享 `ResearchDirectionPolicy` 清洗、限长、标记不可信文本、生成稳定 focus hash 并按当前 Boundary narrowing capability。成功的新 Direction MUST 绑定当前 run/plan/task/loop；它 MUST NOT 创建 TaskSpec、Plan v+1、Run state transition 或递增 `replan_count`。

#### Scenario: 边界内方向被加入

- **WHEN** Agent 提出一个清洗后非空、未重复且不扩大 Boundary 的 Direction
- **THEN** Runtime 持久化 loop-local Direction 并使其可被后续 QUERY 引用
- **AND** `current_plan_version`、Run state version 和 `replan_count` 保持不变

#### Scenario: 重复方向被去重

- **WHEN** 清洗后的 focus hash 已存在于当前 loop
- **THEN** Runtime 记录 `DIRECTION_DEDUPED`
- **AND** 不创建第二个 Direction，也不消耗 `max_directions`

#### Scenario: 边界外方向不能隐式 Replan

- **WHEN** ADD_DIRECTION 需要未批准 capability、额外预算或 Goal/Completion scope 变更
- **THEN** ActionValidator 拒绝该 step
- **AND** 只有后续正式 Focused Replan 或授权 Gate 才能扩大边界

### Requirement: Research loop 以 durable step 推进

`RuntimeTick` SHALL 对每个 active agent-loop Task 每次最多推进一个 ResearchStep。每个 step MUST 具有稳定 logical identity，并按 DECIDING/reserve → decide → PREPARED → execute → accept 协议持久化；DECIDING step、Agent budget reservation 和稳定 decision request ID MUST 在任何 Agent/capability 外部调用前提交。QUERY/ADD_DIRECTION 成功但未停止时，同一 Task SHALL 重新进入可调度非终态，只有被 LoopGuard 接受的 STOP_REQUEST 或确定性耗尽才关闭 loop。

#### Scenario: QUERY step 原子接受

- **WHEN** PREPARED QUERY 的 capability result 通过 Attempt、Lease epoch、Plan version、Run state version 和 step identity fencing
- **THEN** Evidence、Agent/capability usage、coverage、ResearchStep、事件与 checkpoint 在同一事务中接受
- **AND** Task 被重新排队用于下一 step

#### Scenario: Agent reasoning 前先持久化 reservation

- **WHEN** Runtime 准备调用 ResearchAgent 决定当前 step
- **THEN** 同一事务先持久化 DECIDING ResearchStep、Agent usage reservation、Attempt/Lease binding 与稳定 decision request ID
- **AND** 事务未提交时不得调用 Agent

#### Scenario: 进程在 Agent 返回后退出

- **WHEN** Agent 已返回但 typed action/实际 reasoning usage 尚未转为 PREPARED 时进程退出
- **THEN** 恢复使用相同 decision request ID 对账
- **AND** unknown outcome 保守保留或消费已持久化 reservation，不产生未计费的无限重试

#### Scenario: 进程在 step accept 前退出

- **WHEN** capability 已返回但本地 accept transaction 尚未提交时进程退出
- **THEN** 恢复使用相同 logical step 和确定性 request ID reconciliation
- **AND** Evidence 与 usage 最多接受一次

#### Scenario: 不同 seed Task 并行推进

- **WHEN** 同一 Plan 有多个 ready agent-loop seed Tasks
- **THEN** 一个 tick MAY 在 `max_concurrency` 内并行推进这些 Tasks
- **AND** 每个 Task 同时最多存在一个 active ResearchStep

### Requirement: Step retry、Lease 和预算保持有界

ResearchStep SHALL 使用独立的 step retry count；成功的非终止 step MUST NOT 消耗后续 step 的失败重试额度。执行中的 step MUST 持有可 heartbeat 的 Lease。Agent reasoning 和 capability usage MUST 在共享 Run budget 与按 seed loop ceiling 内逐步准入并原子消费，Pause、Resume、Retry 或 Replan MUST NOT 重置任何已消费 usage、step 或 direction 计数。

#### Scenario: 后期 step 首次暂时失败

- **WHEN** 一个 loop 已成功接受多个 steps，而当前新 step 第一次发生 retryable upstream failure
- **THEN** RetryClassifier 使用当前 step 的 retry count，而不是 Task 的历史总 dispatch 次数

#### Scenario: Lease 在 step 执行中续期

- **WHEN** Agent 或 capability 调用持续接近 Lease TTL
- **THEN** Runtime 按 heartbeat 策略续期相同 epoch 的 Lease
- **AND** 续期失败后该执行结果不能被正式接受

#### Scenario: 相同步骤 replay 不双重扣费

- **WHEN** 已接受 step 因 recovery、重复消息或旧 worker 结果再次出现
- **THEN** Runtime 返回/保留既有接受结果
- **AND** 不重复接受 Evidence 或消费 usage

#### Scenario: 全局预算耗尽

- **WHEN** 接受当前已发生 usage 后 Run budget 达到上限
- **THEN** 当前结果按 fencing 规则记录，Run 确定性终止为 `BUDGET_EXCEEDED`
- **AND** 不把预算终止表示为正常 STOP

### Requirement: STOP_REQUEST 不能绕过 ANALYZE acceptance

STOP_REQUEST SHALL 只表达 Worker 请求结束当前 seed loop。LoopGuard MUST 确定性验证 required coverage、独立 Evidence、supporting evidence ownership、无 in-flight step 和计数一致性。所有 required seed loops 关闭后，ResearchPhaseHandler SHALL join accepted EvidenceSet 并进入 ANALYZING；Run MUST NOT 因 STOP_REQUEST 直接进入 WRITING。

#### Scenario: 结构覆盖满足后停止

- **WHEN** STOP_REQUEST 引用当前 loop 的 Evidence，required coverage 已满足且没有 in-flight step
- **THEN** LoopGuard 接受停止并令该 seed Task 成功

#### Scenario: 过早停止被拒绝

- **WHEN** STOP_REQUEST 缺少 required coverage、独立 Evidence 或引用了其他 Plan/Task 的 Evidence
- **THEN** LoopGuard 记录结构化拒绝并重新排队当前 Task
- **AND** Run 不进入 ANALYZING 或 WRITING

#### Scenario: Agent 停止但 ANALYZE 判定缺口

- **WHEN** 所有 loops 关闭并进入 ANALYZING，但 L0/L1 sufficiency 漏斗判定 `research_gap`
- **THEN** Run 不进入 WRITING
- **AND** 现有宏观 Focused Replan 创建 Plan v+1 并消费 `max_replans`

### Requirement: 耗尽、降级和不可信输入均可审计

`max_steps`、`max_directions` 或 loop ceiling 耗尽 SHALL 关闭 loop 为 `EXHAUSTED`，保留已接受 Evidence 与 Degradation 并交给 ANALYZE；Run deadline/global budget 耗尽 SHALL 使用对应 terminal reason。进入 Agent 上下文的 Evidence、Direction 和外部文本 MUST 限长、去重、保留 source/untrusted 标记，并使用有界 digest；结构验证、Router 和状态转换 MUST NOT 依赖模型遵循文本指令。

#### Scenario: max_steps 耗尽

- **WHEN** Agent 未产生可接受 STOP_REQUEST 且达到 `max_steps`
- **THEN** loop 记录 `RESEARCH_LOOP_EXHAUSTED` 与 Degradation
- **AND** 已接受 Evidence 参与 Research join 和后续 ANALYZE

#### Scenario: 不可信 Evidence 包含指令

- **WHEN** Web 或其他不可信 Evidence 包含要求扩大 capability、修改状态或忽略预算的文本
- **THEN** 文本仅作为标记后的 untrusted data 进入有界 digest
- **AND** ActionValidator、Router、BudgetGuard 和状态机继续执行原有正式约束

### Requirement: 新旧 research execution mode 可并存

每个 Plan version SHALL 持久化 `schema_version` 与 `research_execution_mode`。缺失 mode 的旧 Plan MUST 解释为 `fixed_fanout`；`fixed_fanout` 与 `agent_loop` MUST 使用各自兼容 consumer，同一 Plan version 执行中 MUST NOT 由全局配置切换模式。

#### Scenario: 升级后恢复旧 Run

- **WHEN** 升级前创建的 active Plan 没有 research execution mode
- **THEN** Runtime 将其作为 `fixed_fanout` 继续执行
- **AND** 不要求为旧 Plan 创建 ResearchLoop/ResearchStep 记录

#### Scenario: 停止新模式 rollout

- **WHEN** operator 将新 Plan 默认模式切回 `fixed_fanout`
- **THEN** 后续新 Plan 使用旧模式
- **AND** 已存在的 agent-loop Plan 继续由兼容 consumer 完成或被显式取消/重建
