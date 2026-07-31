## Why

当前 RESEARCH 是“先规划、后一次性执行”：Planner 预先生成 research Tasks 及每个 Task 的 `source_hints`，`RuntimeTick` 并行派发 Tasks，而 `MultiSourceResearchHandler` 在单个 Task 内一次性 fan-out 已声明的数据源。Task 无法观察中间 Evidence 后改写下一次 query、补充同一审批边界内的新方向或提前请求停止；只有整轮 RESEARCH 结束后，ANALYZE/REVIEW 才能通过 Plan v+1 的宏观 Focused Replan 补缺口。

这使查询修正、实体消歧、时间范围调整和由 Evidence 揭示的新子方向都被推迟到整轮之后。需要在不扩大已审批权限、预算和研究范围的前提下，为每个 seed research Task 增加可恢复、可审计、逐步计费的 observe → decide → act 微循环。

## What Changes

- RESEARCH 中的每个已接受 `evidence_researcher` Task 成为一个 seed；Agent 在该 Task 的显式 `ExplorationBoundary` 内逐步选择 `QUERY`、`ADD_DIRECTION` 或 `STOP_REQUEST`。
- `RuntimeTick` 对 agent-loop Task 每次只接受一个 durable research step：先持久化 DECIDING/reservation，再选择并持久化 PREPARED action，执行至多一次 capability 调用，原子接受 Evidence/usage/checkpoint，并在未停止时重新排队同一 Task。
- `ADD_DIRECTION` 只创建 plan-scoped、loop-local Direction；它复用抽取后的 gap 清洗与 capability narrowing 安全原语，但不创建 Plan v+1、不修改 `current_plan_version`、不递增 `replan_count`。
- 真正扩大 capability、预算、Completion Contract 或研究范围的请求仍只能走现有宏观 Focused Replan / Human Gate。
- `STOP_REQUEST` 只是 Worker 请求结束研究；确定性结构覆盖守卫通过后才关闭 Task，ANALYZE 的 L0/L1 漏斗仍是进入 WRITING 前的权威接受路径。
- PlanGraph 保留 research-only TaskSpecs。Task description 作为 seed，新增版本化 `ExplorationBoundary`（允许 capability、按 seed 的 required coverage、每 seed 的 `max_steps`/`max_directions`/loop budget ceiling）和 `research_execution_mode`；至少一个 seed 是硬约束，所有 seed 的最坏预算总和不得突破共享 Run budget。
- 保留跨 seed Task 并行。`fixed_fanout` 旧 Plan 继续使用 `MultiSourceResearchHandler`；新 `agent_loop` Plan 每个 Task/每个 tick 至多执行一个 step，避免一个长 Attempt 吞掉完整循环。
- 每个 step 在任何外部调用前持久化稳定逻辑 ID、预算 reservation 与确定性 decision/capability request idempotency key，并具有独立 retry 计数、Lease heartbeat、usage 与结构化事件；进程恢复不得重复接受 Evidence 或双重扣费。

**BREAKING** — 新建 Plan 的 research execution contract 增加 `research_execution_mode` 和 `ExplorationBoundary`；PLAN_APPROVAL 展示并批准 seed、required coverage、capability allowlist、步数/方向上限及预算 ceiling。旧 Plan 通过持久化 mode/schema version 继续按 `fixed_fanout` 执行。

## Capabilities

### New Capabilities

- `research-agent-loop`: 定义审批边界内的 QUERY / ADD_DIRECTION / STOP_REQUEST 微循环、durable step、逐步预算、恢复、结构覆盖守卫及与 ANALYZE 的交接。

### Modified Capabilities

- `dynamic-research-planning`: PlanGraph Proposal 增加版本化 research execution mode 与 ExplorationBoundary；TaskSpecs 作为非空 seed，并由 PlanValidator 校验边界。
- `multi-source-research-routing`: `fixed_fanout` 保持原语义；`agent_loop` 改为每 seed Task 每 tick 至多一个 capability step，同时保留跨 Task 并行和 required/optional coverage。
- `run-lifecycle-coordination`: RESEARCH 只有在所有 seed loops 终止且 EvidenceSet 被持久化后才能进入 ANALYZING；一个 coordinator advance 仍保持有界。
- `durable-orchestration-runtime`: Attempt/Lease/retry/budget/checkpoint 扩展到 durable research step，成功的非终止 step 不消耗失败重试预算。
- `human-gated-orchestration`: PLAN_APPROVAL 明确绑定并展示 seed 与 ExplorationBoundary；边界外扩展仍需正式 Plan/Gate 路径。

## Impact

- Domain/Persistence：`PlanGraph`、`TaskSpec`/Plan Proposal、ResearchLoop/ResearchStep/Direction 记录、repository/UoW、事件与 checkpoint。
- Runtime：`RuntimeTick` 的 research step prepare/execute/accept、per-step retry、Lease heartbeat、budget reservation/consumption、fencing 与 recovery。
- Orchestration：`ResearchPhaseHandler`、Planner/PlanValidator、composition、CapabilityRouter request 构造；现有 ANALYZE Focused Replan 接受语义保持不变。
- Compatibility：持久化 `research_execution_mode`/schema version；旧 Plan 与进行中的 `fixed_fanout` Run 无需转换，新模式可按 Plan 级开关回滚。
- Specs/Tests：动作契约、边界校验、崩溃恢复、幂等、预算、prompt-injection、required coverage、旧 Plan 兼容和端到端自适应研究。
