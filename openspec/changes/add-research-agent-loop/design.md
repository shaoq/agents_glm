## Context

当前 Planner 生成多个 `evidence_researcher` Task；`RuntimeTick` 在 RESEARCHING 中并行 dispatch ready Tasks，每个 `MultiSourceResearchHandler` 再对该 Task 的 `required_capabilities` 做一次多源 fan-out。这个模型有良好的 Task/Attempt/Lease/fencing 与跨 Task 并行，但单个 Task 无法根据中间 Evidence 决定下一次 query。

ANALYZE/REVIEW 已有宏观回环：`research_gap` 经 `FocusedReplanBuilder` 与 `ReplanService` 创建 Plan v+1、新 PENDING Task、`replan_count+1` 并回到 RESEARCHING。该路径是正式 Plan 变更，不适合被每个微观方向复用：在旧 Plan Attempt 执行期间 bump `current_plan_version` 会使其结果在 RuntimeTick accept 时被 fencing 判为 stale。

本变更因此区分三层：

```text
Plan / PLAN_APPROVAL        seed + capability/coverage/budget boundary
          │
          ▼
Research Agent Loop         boundary 内的 QUERY / ADD_DIRECTION / STOP_REQUEST
          │
          ▼
ANALYZE acceptance          L0 deterministic + L1 typed/model-backed

任何 boundary expansion ──▶ formal Focused Replan / Gate / Plan v+1
```

## Goals / Non-Goals

**Goals:**

- 每个 seed research Task 能基于已接受中间 Evidence 调整后续 query 和增加边界内方向。
- 每个 research step 都有 durable identity、checkpoint、Evidence、usage、retry 与可恢复语义。
- 保留 Router、Plan approval、Run budget、max steps/directions、Lease/fencing、ANALYZE、Gates 和 FINALIZE。
- 保留跨 seed Task 并行；每个 Task 的步骤顺序串行，以保证 observe-before-decide。
- Loop-local Direction 不改变 Plan version 或宏观 Replan budget。
- 旧 `fixed_fanout` Plan 在升级后继续执行；新 Plan 显式选择 `agent_loop`。

**Non-Goals:**

- 不允许 Agent 修改 GoalSpec、Completion Contract、Run state、Plan、capability allowlist 或预算上限。
- 不把 STOP_REQUEST 当作进入 WRITING 的充分条件。
- 不在本变更中接入真实 RAG/Memory/Web sibling service。
- 不支持多 Agent 协作或 Agent 间消息。
- 不移除 `MultiSourceResearchHandler`；它继续服务旧 Plan 和显式 `fixed_fanout` Plan。
- 不在本变更中引入单个 step 内的 capability batch；如需恢复 Task 内多源并发，后续增加有界 `QUERY_BATCH`。

## Decisions

### 1. Plan 保持正式审批边界，TaskSpec 作为非空 seed

PlanGraph 新增：

- `schema_version`
- `research_execution_mode: fixed_fanout | agent_loop`
- `exploration_boundary`

`ExplorationBoundary` 至少包含：

- `allowed_capabilities`
- `required_coverage_by_seed`
- 每个 seed loop 的 `max_steps`
- 每个 seed loop 的 `max_directions`
- 可选的每 seed `max_tokens` / `max_cost_usd` ceiling

现有 TaskSpec description 是 seed direction；至少一个 seed 是 PlanValidator 硬约束。Task 的 `required_capabilities` 保持“最低覆盖义务”，不得被误解释为仅 allowlist；它们必须是 Plan boundary allowlist 的子集。step/direction/loop ceiling 按 seed 分别执行；若 Run budget 有有限上限，PlanValidator 还必须验证所有 seed 的最坏 token/cost ceiling 总和不超过可用 Run budget。

**理由：** 这保留现有 research-only Plan、Task materialization、跨 Task 并行和 PLAN_APPROVAL 模型，只增加显式边界，不需要把整个 PlanGraph 替换成另一种结构。

**替代方案：** 允许空 seed 后由 Agent 从 objective 自行创建首方向。拒绝该方案，因为它让 PLAN_APPROVAL 无法审批初始研究范围，并与当前 `task_count > 0` 不变量冲突。

### 2. Action 是强类型 Proposal，正式控制仍由确定性代码接受

`ResearchAgent.decide(ResearchLoopView) -> ResearchAction` 只能返回：

- `QUERY(direction_id, capability_kind, query, rationale)`
- `ADD_DIRECTION(parent_direction_id, hint, rationale)`
- `STOP_REQUEST(reason, supporting_evidence_ids, unresolved_questions)`

每个 action 都绑定 `run_id`、`plan_version`、`task_id`、`loop_id`、`step_id`。模型输出不能提供 capability ID、request ID、Run state 或 Plan mutation；这些由 runtime 构造。

ActionValidator 检查 schema、当前 step、方向存在性、query/hint 长度、capability boundary、required coverage 和预算准入。非法 action 作为 `INVALID_RESPONSE`/`POLICY_VIOLATION` 的 step failure 进入有界 retry，不产生 Evidence、Direction、Plan 或 Run 写入。

### 3. 每个 RuntimeTick 对每个 loop Task 至多推进一个 durable step

一个 agent-loop Task 在多个 step 之间保持非终态。RuntimeTick 扩展为：

1. Dispatch/Reserve：仅 RESEARCHING；claim Task Lease，创建 Attempt，并在同一事务创建 `DECIDING` ResearchStep、Agent reasoning budget reservation 与确定性 `decision_request_id`。任何外部调用前必须已有该 durable record。
2. Decide：使用 `decision_request_id` 调用 ResearchAgent；provider 支持幂等时复用相同 key，不支持时通过 operation record 对账，unknown outcome 按保守 reservation 计费。
3. Prepare：短事务内再次校验 Attempt/Lease/Plan/Run version，把 typed action、实际 reasoning usage与未使用 reservation 的释放原子写入，并将 step 转为 PREPARED。
4. Execute：QUERY 使用另一个确定性 capability request ID，最多调用一次 `router.invoke`；ADD_DIRECTION/STOP_REQUEST 不调用 capability。Lease 在执行期间按 TTL/3 heartbeat。
5. Accept：同一事务接受 step、Evidence、capability usage、Direction/coverage、事件和 checkpoint，释放 Lease：
   - QUERY / ADD_DIRECTION 成功后 Task `DISPATCHED -> PENDING`，等待下一 tick；
   - 被 LoopGuard 接受的 STOP_REQUEST 令 Task `DISPATCHED -> SUCCEEDED`；
   - step failure 只重试当前 step。

同一 tick 可并行推进最多 `RunPolicy.max_concurrency` 个不同 seed Tasks，但单个 Task 同时只能有一个 active step。

**理由：** 避免一个长 Attempt 在 30 秒 Lease 下运行整个 while-loop；每一步都成为恢复边界，同时保留 RuntimeTick 的 dispatch/execute/accept 与 fencing 模型。

### 4. Step retry 与 Task 历史 Attempt 分离

`Task.attempt_count` 继续记录历史 dispatch 次数，但 agent-loop 的 retry admission 使用 `ResearchStep.retry_count`，而不是累计 Task attempt_count。成功接受一个非终止 step 后，下一个 step 从 retry_count=0 开始；Resume 不重置已消费 step、预算或 direction 数量。对同一 logical step 的 Decide 或 QUERY 重试复用原 decision/capability request ID。

**理由：** 一个正常的 8-step loop 不应因为第 8 步第一次暂时性失败而被错误视为“超过每 Task 3 次 Attempt”。

### 5. ADD_DIRECTION 复用安全原语，但不是 Focused Replan

从 `focused_replan.py` 抽取共享的 `sanitize_gap`/文本封装、focus hash 和 capability narrowing 为无持久化副作用的 `ResearchDirectionPolicy`。调用方：

- macro gap：`FocusedReplanBuilder` 使用该 policy 后构造 ReplanProposal；
- micro loop：ADD_DIRECTION 使用该 policy 后创建 loop-local Direction。

Direction 绑定当前 Plan/Task/boundary，并用 focus hash 去重。重复方向记录 `DIRECTION_DEDUPED`，不增加 `max_directions` 消耗。Direction 不能产生 TaskSpec、Plan write、Run transition 或 `replan_count` 变化。

如果 hint 需要 boundary 外 capability 或改变 scope，ActionValidator 拒绝；Agent 不能在 loop 内自动扩大边界。后续 ANALYZE 若仍判 gap，继续走现有宏观 Focused Replan。

### 6. STOP_REQUEST 由结构 LoopGuard 接受，ANALYZE 仍是权威漏斗

STOP_REQUEST 表示 Agent 不建议继续查询，不等于 Evidence 已足够支持最终 Analysis。LoopGuard 只做确定性结构检查：

- required coverage 已满足或有允许的显式 degradation；
- supporting evidence IDs 属于当前 loop；
- 至少存在 required research 的独立 Evidence；
- 没有未接受/执行中的 step；
- step/direction/budget 计数一致。

LoopGuard 接受后 Task 成功；拒绝则记录原因并重新排队，直到后续 STOP_REQUEST 或边界耗尽。所有 seed Tasks 终止后，ResearchPhaseHandler 加载已接受 Evidence，确定性 join 并进入 ANALYZING。

ANALYZE 保持现状：

- L0 对零独立 required Evidence 做确定性 research gap；
- L1 `EvidenceSufficiencyReviewer` 使用 `AnalysisArtifact + EvidenceSet` 做 typed、model-backed 权威判断；
- 只有 ANALYZE 接受后进入 WRITING。

### 7. 每步预算准入与原子消费

Loop budget 是共享 Run budget 内的 ceiling，不是新资金池，不会在 retry、resume 或 Replan 时重置。每步 budget flow：

1. Dispatch/Reserve 事务检查 deadline、Run remaining budget、loop remaining budget 和 step cap，并持久化 Agent reasoning 的最大 token/cost reservation。
2. Decide 完成后原子接受实际 reasoning usage，释放未使用 reservation；unknown outcome 保守保留或消费 reservation，避免免费重试。
3. QUERY 前对 capability descriptor/request 上限做第二次 durable reservation。
4. Accept 时原子写入实际 capability usage，并释放未使用 reservation。
5. 相同 `step_id`/operation id 的 replay 返回已接受 usage，不重复扣费。

如果实际 usage 使全局 Run budget 到达上限，当前已发生的结果按 fencing 规则保存，Run 在 accept 后确定性终止为 `BUDGET_EXCEEDED`，不得伪装成正常 STOP。仅 loop ceiling 或 `max_steps` 耗尽时，Loop 以 `EXHAUSTED`/degradation 结束并把已有 Evidence 交给 ANALYZE。

### 8. 幂等、恢复与不可信输入

- DECIDING/PREPARED ResearchStep 的 logical key 是 `(run_id, plan_version, task_id, step_index)`。
- Agent decision request ID 与 Capability request ID 分别从 logical step ID 派生；恢复使用相同 request ID，provider adapter/operation repository 必须返回已记录结果或安全 reconciliation。
- 旧 Lease 的结果作为 observation 保留，但不能接受 Evidence、usage 或 Direction。
- Evidence/Direction 文本进入 Agent prompt 前必须限长、去重、按 source 标记并置于明确 untrusted block；结构验证与 Router boundary 不依赖 prompt 遵循。
- LoopView 使用有界 evidence digest 和引用 ID，不把无限增长的原文直接拼入上下文。

### 9. 兼容模式与回滚

Plan 持久化 `schema_version` 和 `research_execution_mode`：

- 缺失 mode 的旧 Plan 解释为 `fixed_fanout`；
- `fixed_fanout` 使用现有 MultiSourceResearchHandler；
- `agent_loop` 使用 ResearchAgentLoop handler/RuntimeTick step path；
- 同一个 Plan version 的 mode 不可在执行中切换。

Composition 可控制“新 Plan 默认 mode”，但消费端始终根据持久化 Plan mode 分流，不能用全局 feature flag 改变进行中 Run 的语义。

回滚时停止创建新 `agent_loop` Plan；已经开始的 agent-loop Plan 继续由兼容 consumer 完成或经显式取消/重建处理，不能直接交给旧 handler。

### 10. 结构化可观测性

至少记录：

- `RESEARCH_LOOP_STARTED`
- `RESEARCH_STEP_PREPARED`
- `RESEARCH_ACTION_ACCEPTED/REJECTED`
- `RESEARCH_QUERY_ACCEPTED`
- `RESEARCH_DIRECTION_ADDED/DEDUPED`
- `RESEARCH_STOP_REQUESTED/REJECTED`
- `RESEARCH_LOOP_EXHAUSTED/COMPLETED`

事件包含 run/plan/task/loop/step/direction/request/evidence IDs、action kind、usage、coverage 和安全诊断；不记录原始未清洗 prompt、秘密或完整外部正文。

## Risks / Trade-offs

- **[单 capability step 降低 Task 内并行]** → 保留跨 seed Task 并行；首版优先自适应与恢复，后续单独设计有界 QUERY_BATCH。
- **[RuntimeTick 复杂度增加]** → 以独立 ResearchLoop/ResearchStep aggregate 和 repository 隔离；普通 Task 保持原 accept path。
- **[外部调用成功但本地 accept 前崩溃]** → 稳定 request ID + PREPARED step + operation reconciliation，禁止仅靠重新调用猜测结果。
- **[预算 reservation 过于保守]** → reservation 上限配置化并记录预测/实际差异，但不得放宽全局 Run budget。
- **[Prompt injection 影响 Agent 选择]** → untrusted framing、typed actions、ActionValidator、Router 和硬计数共同限制正式效果；不声称纯文本清洗能消除模型诱导。
- **[旧新模式并存增加维护成本]** → mode 持久化、共享 Evidence/Router/Join 契约，并设置后续淘汰 fixed_fanout 的独立 change。

## Migration Plan

1. 先增加向后兼容的 Plan mode/boundary model、ResearchLoop/Step persistence 和只读 inspection。
2. 实现 step prepare/execute/accept、budget/lease/recovery，并保持默认新 Plan 为 `fixed_fanout`。
3. 接入 ResearchAgent/ActionValidator 与 loop-local Direction；运行 deterministic、failure-injection 和 legacy tests。
4. 让 Planner 可生成 `agent_loop` Plan，并使 PLAN_APPROVAL 展示 boundary。
5. 通过配置只对新 Plan 启用 `agent_loop`；观察 usage、recovery、STOP rejection 和 gap rate。
6. 回滚时停止新建 agent-loop Plan，保留兼容 consumer 处理已存在 Plan。

## Open Questions

无阻断实现的问题。单 step capability batching、是否最终淘汰 `fixed_fanout`、以及按 seed 分配独立预算份额留给后续 change。
