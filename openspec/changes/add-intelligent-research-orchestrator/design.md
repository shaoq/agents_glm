## Context

仓库当前有两个可独立运行的 Agent 组件子项目：

- `agents_memory` 提供结构化写入、召回和维护能力，SQLite 是真相源；
- `agents_rag` 提供知识构建和查询管线，保留检索来源、引用和降级信息。

`agents_orchestration` 目前只有知识与已确认架构设计，没有 Python 包、Runtime 或应用入口。
本 change 建设首个应用“智能研究与任务执行台”，用一个真实研究闭环验证编排知识，而不是先建设
抽象的通用 Agent SDK。

主要约束：

- Python 3.12、本地优先；
- 首期交付 Python Service API + CLI；
- 同级子项目保持独立，不共享配置、数据库和运行时对象；
- LLM 只能提出语义 Proposal，正式状态由确定性组件提交；
- 首期只读，不引入发布、支付、邮件、部署、文件修改和代码执行；
- 默认测试不调用真实网络；
- Runtime 必须支持进程重启恢复、Gate、Retry、Deadline、Budget 和 Late Result 防护；
- 评测平台不属于本 change，但实现必须产出可用于后续评测的结构化 Event、Artifact 和 Usage。

上游架构设计：

`agents_orchestration/docs/specs/2026-07-27-intelligent-research-orchestrator-design.md`

## Goals / Non-Goals

**Goals:**

- 从自然语言研究目标创建版本化 GoalSpec 与 Completion Contract；
- 生成并校验有界动态 PlanGraph；
- 以 Run、Task、Attempt 分层身份执行和恢复工作；
- 通过 Capability Registry/Router 切换 Memory、RAG、Web、Model 和 Fake Adapter；
- 并行执行独立研究 Branch，并形成带来源、冲突和充分性的 EvidenceSet；
- 支持报告分析、写作、审查、有界修订与 Replan；
- 支持四类版本绑定、单次消费的 Human Gate；
- 以 SQLite、Event、Checkpoint、Outbox 和 Artifact Store 实现 Durable Runtime；
- 提供可脚本化的 Python API 与 CLI；
- 生成 `report.md`、`report.json` 和 `run-summary.json`。

**Non-Goals:**

- FastAPI、Web UI、多租户和高并发服务；
- 分布式队列、跨机器 Worker 和多 Watch 进程；
- 第三方 Workflow Engine 作为领域核心；
- 通用 Agent SDK；
- 真实写副作用；
- 自动 Memory 写入和 RAG 索引维护；
- 完整 Agent 评测平台；
- PDF、Word 或 Web Renderer。

## Decisions

### 1. 使用模块化单体与 Ports/Adapters

采用单一 `agents_orchestration` Python 包，内部按 Application、Domain、Control Plane、Runtime、
Worker、Capability 和 Adapter 分层。

依赖方向：

```text
CLI
→ Application
→ Domain + Runtime Ports
← Infrastructure Adapters
```

Domain 不导入 Typer、SQLite、OpenAI SDK、`agents_memory`、`agents_rag` 或 Web Provider。
Memory/RAG 只允许在对应 Adapter 中导入其公开 API。

**理由：**

- 当前吞吐和部署目标不需要分布式复杂度；
- 模块化单体更适合验证状态机、恢复和故障注入；
- Ports/Adapters 保留未来切换 HTTP、Queue 或 Workflow Runtime 的能力。

**替代方案：**

- 全部组件通过内部 Event Bus 通信：扩展灵活，但首期一致性和调试成本过高；
- LangGraph/Temporal 作为领域核心：可快速获得执行能力，但会让 Task、State 和 Completion
  语义依赖框架。

### 2. 使用确定性 Runtime 承载 LLM-assisted Orchestrator

以下模块可以调用 Model Port：

- GoalNormalizer；
- Planner；
- SemanticReviewer；
- WorkerExecutor 中的模型型 Worker。

这些模块只能输出 Proposal。PlanValidator、Scheduler、StateMachine、LeaseManager、BudgetGuard、
GateManager 和 TerminationGuard 的硬约束部分保持确定性。

```text
LLM Proposal
→ Schema Validation
→ Policy Validation
→ State Version Validation
→ Deterministic Commit
```

**理由：** 防止模型绕过权限、预算、状态机、Completion Contract 和终止边界。

**替代方案：** 完全开放式 Agent Loop；灵活但不可稳定恢复和测试。

### 3. 使用 Run、Task、Attempt 与 Operation 四层身份

- Run：一次目标生命周期，Resume 不改变；
- Task：稳定业务工作，Retry 不改变；
- Attempt：每次执行新建；
- Operation：一次外部 Capability Call，用于去重、诊断和未知结果处理。

Replan 创建新的 Plan Version，旧 Task 只能显式保留或 Supersede。Attempt Result 必须验证当前
Task、Plan Version、State Version 和 Lease Epoch 后才能接受。

**理由：** 区分业务工作、执行尝试和外部调用，才能正确处理 Retry、Resume、Replan 和 Late
Result。

### 4. 使用 SQLite Snapshot + Event + Checkpoint + Outbox

SQLite 保存正式状态和以下逻辑记录：

```text
runs
goal_versions
completion_contracts
plan_versions
tasks
task_dependencies
attempts
leases
capability_calls
gates
checkpoints
events
outbox
artifact_metadata
request_deduplication
```

同一事务提交：

```text
State Version
+ Task / Attempt Transition
+ Checkpoint
+ Domain Event
+ Outbox Record
```

大型内容先写入不可变 Artifact，再将带 Hash 的 ArtifactRef 纳入事务。

**理由：**

- Current Snapshot 支持快速查询；
- Event 支持因果、审计和恢复诊断；
- Semantic Checkpoint 提供恢复闭包；
- Outbox 避免状态已提交但通知丢失。

**替代方案：**

- 只保存 Snapshot：无法充分解释和恢复并发事件；
- 完全 Event Sourcing：首期 Replay、版本和开发复杂度过高。

### 5. Runtime 使用可重入 Tick

一个 Tick：

```text
load Run + State Version
→ process Event / Gate Response / Timer
→ expire Lease / Deadline
→ accept or reject Attempt Result
→ recompute Ready Tasks
→ apply Policy / Budget / Concurrency
→ claim and execute bounded work
→ evaluate Replan / Completion / Pause / Termination
→ atomic commit
```

默认 `run start` 创建并持续推进一个 Run，直到终态或阻塞。运维命令：

```text
runtime tick RUN_ID
runtime watch --run RUN_ID
runtime watch
```

首期只支持一个持续 Watch 进程；`runtime tick` 必须指定 Run。

**理由：** Tick 能独立恢复和测试，也支持未来将执行承载迁移到其他 Runtime。

### 6. Worker Definition 与 Capability 分离

首期 Worker：

- ResearchPlanner；
- EvidenceResearcher；
- Analyst；
- ReportWriter；
- ReportReviewer。

WorkerDefinition 包含 Role、Input/Output Contract、Prompt/Policy Version、Allowed Capabilities 和
Budget。Worker 只能提交 TaskResult 或 Proposal。

CapabilityDescriptor 包含 ID、Version、Schema、权限、成本、Timeout、并发、Adapter 和健康状态。
Task Capability 首期包括 Memory、RAG、Web；Model 是受控 Internal Capability；Repository、
Artifact、Clock 和 Event Publisher 是 System Port。

**理由：** Worker 表达“谁以什么角色做事”，Capability 表达“允许调用什么能力”，两者分离
才能复用、路由和安全治理。

### 7. Adapter 统一映射结构化结果与失败

所有 Task Capability 采用 Async Port：

```text
invoke(CapabilityRequest) → CapabilityResult
```

现有同步 `MemoryService` 和 `QueryPipeline` 由 Adapter 受控包装。CapabilityResult 必须包含：

- Status；
- Data / Evidence / Citation；
- Source 和时间；
- Usage；
- Degradation；
- FailureCode、Retryable、Retry-After；
- Operation ID 和 Outcome Certainty。

Orchestrator 不读取 sibling 数据库或 `.env`。

**理由：** Async Port 支持并行研究，统一结果避免 Core 依赖不同 Provider 语义。

### 8. 采用固定外层生命周期与动态内层 PlanGraph

外层阶段：

```text
Goal Normalization
→ Planning
→ Research
→ Evidence Analysis
→ Report Writing
→ Review
→ Final Verification
```

Planner 可以动态创建研究 Task 和依赖，但 PlanValidator 必须验证 DAG、注册能力、权限、预算、
最大 Task、Depth 和 Required Deliverable 覆盖。

默认系统上限：

| 限制 | 默认值 |
|---|---:|
| 最大 Task 数 | 32 |
| 最大 Plan Depth | 4 |
| 最大并发 Task | 4 |
| 每 Task 最大 Attempt | 3 |
| 最大 Replan 次数 | 2 |
| 最大 Report Revision 次数 | 2 |
| 默认 Run Deadline | 30 分钟 |

Run Policy 只能在系统允许范围内调整。

### 9. Branch 独立提交，Join 按证据语义聚合

研究阶段可以 Fan-out 到：

- Memory Lane：默认 Optional；
- RAG Lane：按 Goal Policy 可为 Required；
- Web Lane：默认禁用，Run Policy 显式允许后启用。

每个 Branch 具有稳定 ID 并独立提交 Accepted Result。Join 只读取当前 Plan 的有效 Branch 和
Accepted Attempt，执行来源去重、新鲜度判断、冲突标记、Required/Optional 聚合和 Sufficiency
计算。

**理由：** 避免单个失败 Branch 迫使其他成功分支重跑，也避免用完成数量掩盖必要证据缺失。

### 10. Replan 使用新版本并精确失效

触发条件：

- 必要证据缺口；
- 关键证据冲突；
- Required Capability 不可用；
- Plan 约束失效；
- Reviewer 返回 `RESEARCH_GAP`；
- Gate 修改目标或范围。

```text
Replan Proposal
→ Plan Validation
→ PlanGraph v(N+1)
→ Preserve unaffected accepted results
→ Supersede invalidated tasks
→ Add focused tasks
```

Replan、Revision、Retry 共享同一 Run Budget，不能重置 Deadline。

### 11. Human Gate 绑定版本并单次消费

Gate 类型：

- GOAL_CLARIFICATION；
- PLAN_APPROVAL；
- CONFLICT_RESOLUTION；
- FINAL_REVIEW。

GateRequest 绑定 Gate、Run/Task、Actor/Role、Scope、State Version、Plan Version、Artifact Hash、
期限和允许响应 Schema。Response Event 使用 Request ID 去重，成功消费后不能再次恢复。

**理由：** 防止旧审批作用于新计划/Artifact，或重复回调导致重复执行。

### 12. 首期通过 Registry 物理限制为只读

Registry 不注册发布、邮件、支付、部署、代码执行和文件修改 Capability。Memory Adapter 仅调用
Recall，RAG Adapter 仅调用 Query，Web Adapter 仅执行读取。

外部文档、网页和 Memory 文本全部视为不可信 Evidence，不得转换为 Control Instruction 或
权限。

Secret 只在 Adapter 边界读取，不写入 Prompt、State、Event、Checkpoint、Artifact 或日志。

**理由：** 首期安全性由能力集合和确定性 Policy 强制，不依赖 Prompt 自律。

### 13. 明确降级而不伪造成功

| 失败能力 | 允许策略 |
|---|---|
| Memory | 标记缺少个性化背景后继续 |
| RAG | Policy 允许时使用 Web，并披露本地知识不可用 |
| Web | 使用 RAG/Memory，并披露时效限制 |
| Model | 有界 Retry 或切换兼容 Model Profile |
| Evidence | Partial、Unknown、Gate 或 Fail |

降级必须进入 CapabilityResult、Event、Run Summary 和最终报告。Required Evidence 不足时不能
提交干净 `SUCCEEDED`。

### 14. 最终交付使用不可变 Artifact

最终输出：

- `report.md`；
- `report.json`；
- `run-summary.json`。

Final Verification 必须绑定 Candidate State Version，检查 Completion Contract、Required Task、
Evidence Sufficiency、冲突、未决 Attempt、降级披露和 Deadline，再通过 Compare-and-Set 提交
终态。

### 15. CLI 与 Python API 共用 Application Service

CLI 只做参数适配和展示。变更命令使用 Request ID 或 Expected Version。

主要命令：

```text
run start/show/watch/pause/resume/cancel
gate list/respond
artifact list/export
capability list/doctor
runtime tick/watch
```

主要 Python API：

```text
start_run
run_until_blocked
get_run
pause_run
resume_run
cancel_run
respond_gate
export_artifacts
```

## Risks / Trade-offs

- **[SQLite 写并发和锁竞争]** → 首期只支持一个持续 Watch 进程，事务保持短小，Capability 调用
  不在写事务内执行，并使用 State Version 和 Lease Epoch 条件更新。
- **[LLM 产生非法或过大 Plan]** → 严格 Schema、PlanValidator、系统硬上限和 Plan Approval。
- **[同步 Memory/RAG 阻塞 Async Runtime]** → Adapter 使用受控线程包装和并发限制；Core 始终面对
  Async Port。
- **[Web 内容 Prompt Injection]** → 内容只进入 Evidence 字段，Control Instruction 与
  Capability Request 只能由结构化 Plan/Task 产生。
- **[Artifact 已写入但 SQLite 事务失败]** → Artifact 不可变且内容寻址；后台/维护命令可清理
  未被 metadata 引用的孤儿。
- **[Capability Timeout 后结果不确定]** → 保存 Operation ID 和 Outcome Certainty；不可安全确认
  时进入 Unknown/RecoveryRequired，不盲目重试。
- **[Replan 造成重复工作或历史漂移]** → Plan 版本化、依赖失效分析、保留 Accepted Result、旧
  Task 显式 Supersede。
- **[Gate 长期无人响应]** → 持久化 Expires At、Deadline 和升级策略；到期后进入 Expired、
  Escalated、Partial 或 Failed。
- **[本地 Watch 进程不是服务级调度器]** → 首期明确单进程范围；未来通过 Runtime Port 替换为
  Queue/Workflow Adapter。
- **[缺少完整评测体系]** → 本 change 只建设结构化 Evidence、Event、Usage 和确定性测试；质量
  评测在后续独立 change 中完成。

## Migration Plan

这是全新子项目功能，不迁移现有业务数据，也不修改 Memory/RAG Schema。

交付采用可回滚的渐进顺序：

1. 建立独立 Python 包、Domain Model、Port 和 Fake；
2. 完成 SQLite Repository、Artifact Store 和状态机；
3. 完成 Runtime Tick、Checkpoint、Lease、Recovery 和 Outbox；
4. 完成 Goal、Plan、Validator、Scheduler 和有界 Replan；
5. 完成 Worker、Capability Registry/Router 和 Fake Adapter；
6. 接入 Memory、RAG、Web、Model Adapter；
7. 完成 Gate、报告闭环、CLI 和 E2E；
8. 显式启用真实 API Smoke Test。

Rollback：

- change 实现全部位于新 `agents_orchestration` 包，不影响 sibling；
- 可停止 Runtime 进程并保留 SQLite/Artifact 供诊断；
- 若某个真实 Adapter 不稳定，可通过 Registry 切回 Fake 或禁用；
- 不运行 Orchestrator 时，Memory/RAG 现有 CLI 和 Service 行为不变。

## Open Questions

当前没有阻塞实施的开放问题。以下扩展已明确不进入首期，未来必须通过独立 OpenSpec change
设计：

- FastAPI/Web UI；
- 多 Watch 进程和分布式 Worker；
- 真实写副作用及其 Effect Ledger、补偿与审批；
- 多租户 ACL；
- 独立可观测与评测平台；
- PDF/Word/Web Artifact Renderer。
