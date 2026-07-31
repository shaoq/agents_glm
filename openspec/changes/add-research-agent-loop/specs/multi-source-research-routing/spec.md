## MODIFIED Requirements

### Requirement: 确定性 capability 映射与三层边界守卫

系统 SHALL 把 Planner 的 `source_hints` 确定性映射成 `(CapabilityKind, BranchRole)`，并经映射过滤、PlanValidator/ExplorationBoundary、Router policy 三层边界守卫。对 `fixed_fanout` Plan，映射结果继续作为一次性多源 Branch；对 `agent_loop` Plan，required hints SHALL 成为 seed 的最低 coverage，Boundary allowlist 决定 Agent 可选择的 QUERY capability。LLM MUST NOT 直接输出 capability ID 或扩大正式权限。

#### Scenario: 正常 agent-loop 映射

- **WHEN** seed Task 的 `source_hints = [local_knowledge, live_web]` 且 Boundary 允许对应 capability
- **THEN** 确定性代码将其映射为 required/optional coverage
- **AND** 每个 Agent QUERY 仍经 Boundary 与 Router 检查

#### Scenario: web 被策略禁用

- **WHEN** source_hints 或 Agent action 选择 live_web 但 `RunPolicy.web_enabled = False`
- **THEN** Planner 映射过滤或 ActionValidator/Router 拒绝 WEB_RESEARCH
- **AND** Agent 文本不能覆盖该结果

#### Scenario: capability 未注册或未允许

- **WHEN** 映射或 ExplorationBoundary 中的 capability 不在 registry、system allowlist 或 effective Run Policy
- **THEN** PlanValidator 在 Plan 接受前拒绝；执行期越界 action 在调用 adapter 前拒绝

#### Scenario: required coverage 未满足

- **WHEN** Agent 请求 STOP 但 seed 的 required coverage 尚未满足且没有允许的 degradation
- **THEN** LoopGuard 拒绝 STOP，Task 继续保持非终态直到覆盖满足或边界耗尽

### Requirement: handler 内多源 Branch 并发与 EvidenceJoin

`fixed_fanout` research handler SHALL 保持按 `task.required_capabilities` 构造 Branch、并发调用和 EvidenceJoiner 汇总的既有行为。`agent_loop` handler SHALL 每个 ResearchStep 至多 QUERY 一个 Boundary 内 capability，并把跨 steps 接受的 Evidence 累积到同一 seed loop；所有 seed loops 终止后，ResearchPhaseHandler SHALL 使用同一 EvidenceSet join 契约汇总。

#### Scenario: fixed_fanout 多源并发检索

- **WHEN** fixed_fanout Task 带 `(RAG_SEARCH, WEB_RESEARCH)`
- **THEN** MultiSourceResearchHandler 并发调用 RAG 与 Web adapter 并汇总 Evidence

#### Scenario: agent_loop 顺序观察

- **WHEN** agent-loop Task 的 step N 接受了 Evidence
- **THEN** step N+1 的 LoopView 包含该 Evidence 的有界 digest 与 identity
- **AND** step N+1 才能基于观察选择下一 QUERY 或 ADD_DIRECTION

#### Scenario: agent_loop capability 仍经 Router

- **WHEN** QUERY action 被 ActionValidator 接受
- **THEN** Runtime 用 registry 将 capability kind 解析为 capability ID
- **AND** request 经 CapabilityRouter 路由到 adapter

### Requirement: 多源 task 并行执行

`RuntimeTick` SHALL 用 `asyncio.gather` 与 `asyncio.Semaphore(max_concurrency)` 并行推进一批 ready research Tasks。`fixed_fanout` Task 保持一次 Attempt 内的多源并发；`agent_loop` Task 每次 tick 至多推进一个 ResearchStep，但不同 seed Tasks MAY 并行。两种模式都 MUST 保留 Lease、fencing、budget、recovery 和批量 accept 的可靠性保证。

#### Scenario: 多个 agent-loop seed 并行推进

- **WHEN** 一个 tick 选中多个 ready agent-loop Tasks
- **THEN** 它们在 Semaphore 限制内各推进至多一个 step
- **AND** 同一 Task 不并行执行两个 steps

#### Scenario: 并发度受控

- **WHEN** `max_concurrency = 2` 且有 4 个 ready research Tasks
- **THEN** 同时至多 2 个 Task/step 在执行

#### Scenario: 并发崩溃可恢复

- **WHEN** 并发执行中进程崩溃
- **THEN** 未接受的 Task/ResearchStep 由 Lease expiry 与稳定 logical step 恢复
- **AND** 已接受 Evidence/usage 不重复，late result 不能改变正式状态
