# 智能研究与任务执行台 · Orchestrator 架构设计

| 项 | 值 |
|---|---|
| 日期 | 2026-07-27 |
| 状态 | 架构设计已确认；OpenSpec `add-intelligent-research-orchestrator` 已创建，待实施 |
| 子项目 | `agents_orchestration` |
| 首期交付 | 独立 Python 包 + Python Service API + CLI |
| 运行方式 | 本地优先、单进程执行、SQLite Durable Runtime |
| 业务闭环 | 复杂研究目标 → 动态任务图 → 多源证据 → 分析与审查 → 带引用报告 |

---

## 0. 文档定位

本文将 `agents_orchestration/docs/knowledge/` 中的编排知识转化为首个 Orchestrator
应用架构，回答：

- 首期应用解决什么业务问题，明确不解决什么；
- 确定性 Runtime、LLM Orchestrator、Worker 与 Capability 如何分工；
- 如何以可切换 Adapter 接入 `agents_memory`、`agents_rag` 和 Web Research；
- Run、Task、Attempt、Plan、Checkpoint 和 Artifact 如何建模；
- 动态规划、并行研究、Join、审查和有界重规划如何运行；
- 如何支持持久化、进程重启恢复、人工 Gate、降级和安全控制；
- Python API、CLI、配置、错误模型和验收边界是什么。

本文是上游架构设计，不直接授权实现。对应 OpenSpec change
`add-intelligent-research-orchestrator` 已创建；后续应以该 change 的 specs、design 和 tasks
作为实施依据。

---

## 1. 已确认的关键决策

### 1.1 应用场景

首期建设“智能研究与任务执行台”：

```text
用户复杂研究目标
→ 目标规范化
→ 动态规划
→ Memory / RAG / Web 并行研究
→ 证据分析
→ 报告生成与审查
→ 带引用、限制和运行摘要的交付物
```

首期只执行研究类只读能力，不执行发布、邮件、支付、部署、文件修改或代码运行等真实写
副作用。

### 1.2 交付形态

首期提供：

- Python `OrchestrationService`；
- Typer CLI；
- 本地 SQLite Durable Runtime；
- 本地 Artifact Store；
- Fake Adapter 驱动的默认测试；
- 显式启用的真实 API Smoke Test。

首期不提供 FastAPI、Web UI 和分布式 Worker。

### 1.3 编排方式

采用：

```text
确定性 Runtime
+ LLM-assisted Orchestrator
+ 有界动态 PlanGraph
```

外层 Run 生命周期、状态机、权限、预算、Checkpoint、Gate 和终止语义保持确定；LLM 可以
提出 GoalSpec、PlanGraph、Review 和 Replan Proposal，但不能直接修改正式状态。

### 1.4 能力接入

采用可切换的进程内 Capability Adapter：

```text
agents_orchestration
    → Capability Port
        → Memory Adapter → MemoryService
        → RAG Adapter    → QueryPipeline
        → Web Adapter    → Web Provider
```

Orchestrator 不读取同级子项目的数据库、`.env`、配置对象和内部存储。现有子项目不反向依赖
Orchestrator。

### 1.5 Durable Runtime

采用：

```text
SQLite 正式状态
+ Append-only Domain Events
+ Current Snapshot
+ 语义 Checkpoint
+ Transactional Outbox
+ 本地不可变 Artifact
```

首期就支持在语义 Checkpoint 后退出进程并安全恢复，不先建设只存在于内存中的临时 Runtime。

### 1.6 Human-in-the-loop

支持四类可配置 Gate：

- `GOAL_CLARIFICATION`；
- `PLAN_APPROVAL`；
- `CONFLICT_RESOLUTION`；
- `FINAL_REVIEW`。

默认尽量自动执行；Gate 是否触发由系统安全规则和 Run Policy 决定。

### 1.7 研究能力

首期能力集合：

- Memory Recall；
- RAG Knowledge Search；
- 可选 Web Research；
- Evidence Analysis；
- Report Writing；
- Report Review。

Web 默认关闭，只有 Run Policy 显式允许时才能启用；允许域和数据源范围可以进一步收紧。

### 1.8 最终交付物

每个成功或部分成功的 Run 至少输出：

- `report.md`：人类可读、带引用的研究报告；
- `report.json`：结构化结论、引用、充分性、冲突、限制和未解决问题；
- `run-summary.json`：Plan、Task/Attempt、成本、降级和终止原因摘要。

PDF、Word 和 Web 页面渲染留到后续 Artifact Renderer。

---

## 2. 目标、非目标与成功标准

### 2.1 核心目标

1. 把自然语言复杂研究目标规范化为版本化 GoalSpec 和 Completion Contract；
2. 动态生成可校验、可持久化、可重规划的 PlanGraph；
3. 通过 Worker 调度 Memory、RAG、Web 和模型能力；
4. 并行执行独立研究任务并按证据语义 Join；
5. 保留来源、时间、新鲜度、冲突、充分性和降级信息；
6. 在进程重启、能力失败、重复响应和迟到结果条件下维持状态不变量；
7. 生成带引用、限制说明和执行摘要的报告；
8. 让能力实现可替换而不改变 Core 编排逻辑。

### 2.2 首期非目标

- 通用 Agent SDK 或通用分布式 Workflow Engine；
- Web UI、多租户服务、高并发 API；
- 跨机器队列和分布式 Worker；
- 任意代码执行和桌面控制；
- 发布、支付、邮件、部署等真实写副作用；
- 自动写入或修改长期 Memory；
- 直接维护 RAG 索引；
- 完整 Agent 评测平台；
- PDF、Word 或 Web 报告渲染。

### 2.3 首期成功标准

首期完成需要同时满足：

1. JSON Goal 可以创建 Durable Run；
2. Planner 生成的动态 PlanGraph 必须经过确定性校验；
3. Memory、RAG、Web Fake Adapter 能够并行运行并汇聚为 EvidenceSet；
4. 能生成三类最终交付物；
5. 进程重启后能够从正式状态继续；
6. Gate 响应绑定版本且最多消费一次；
7. 证据缺口可以触发有界 Replan；
8. 能力失败产生明确的 Degraded、Partial 或 Failed 状态；
9. Capability 实现可以经 Registry 切换；
10. 首期不存在真实写副作用路径。

---

## 3. 架构路径

### 3.1 采用方案

采用：

```text
模块化单体
+ Ports / Adapters
+ SQLite Durable Runtime
+ Append-only Domain Events
+ Transactional Outbox
```

首期最重要的是验证编排语义、状态恢复和能力边界，而不是提前承担分布式一致性与运维成本。

### 3.2 未采用方案

#### 内部 Event Bus 驱动全部组件

事件驱动插件架构扩展性强，但首期会显著增加状态一致性、追踪和测试复杂度。当前只保留
Domain Event 和 Outbox，不把所有本地函数调用异步化。

#### 第三方 Workflow Framework 作为领域核心

LangGraph、Temporal 等未来可以成为 Runtime Adapter，但不能成为 Task、State、Capability、
Completion 和 Policy 的领域定义来源。这样可避免框架语义侵入 Core，并保留切换能力。

---

## 4. 系统边界与依赖规则

### 4.1 总体架构

```text
CLI / Python API
        │
        ▼
OrchestrationService
        │
        ├──────────── Control Plane ────────────┐
        │  Goal / Plan / Validate / Schedule    │
        │  Review / Replan / Termination        │
        │                                       │
        └──────────── Runtime Plane ────────────┤
           State Machine / Lease / Budget       │
           Checkpoint / Recovery / Gate / Event │
                                                ▼
                                    Worker Registry / Executor
                                                │
                                                ▼
                                 Capability Registry / Router
                                     │        │        │
                                     ▼        ▼        ▼
                                   Memory    RAG      Web
                                     │        │        │
                                     └── Infrastructure Adapters
                                                │
                                                ▼
                                SQLite / Artifact / Model / Clock
```

### 4.2 依赖方向

```text
CLI / API
→ Application
→ Domain + Runtime Ports
← Infrastructure Adapters
```

必须保持：

1. Domain 不依赖 Typer、SQLite、OpenAI SDK、Memory、RAG 或 Web SDK；
2. Application 不包含状态转换和权限规则；
3. Runtime 通过 Repository、Artifact、Clock 和 Event Port 访问基础设施；
4. Adapter 将外部对象映射为 Orchestrator 自有 DTO；
5. `agents_memory` 和 `agents_rag` 不依赖 `agents_orchestration`；
6. Orchestrator 不共享或直连同级子项目的存储。

---

## 5. 核心模块与职责

### 5.1 Application Layer

#### `OrchestrationService`

提供稳定 Use Case：

```text
start_run
run_until_blocked
get_run
pause_run
resume_run
cancel_run
respond_gate
list_artifacts
export_artifacts
```

它负责命令校验、事务用例编排和 DTO 转换，不负责 Goal 解释、Plan 生成、调度规则和状态机。

#### Command / Query DTO

所有 CLI 和未来 API 都使用可序列化 DTO，不直接暴露内部 ORM、Adapter 或存储对象。

### 5.2 Control Plane

| 模块 | 类型 | 职责 |
|---|---|---|
| `GoalNormalizer` | LLM-assisted | 把用户输入转为 GoalSpec、约束和 Completion Contract Proposal |
| `Planner` | LLM-assisted | 生成或修订 PlanGraph Proposal |
| `PlanValidator` | Deterministic | 校验 DAG、能力、权限、预算、深度和完成覆盖 |
| `Scheduler` | Deterministic | 从正式 State 计算 Ready Tasks |
| `SemanticReviewer` | LLM-assisted | 提出证据缺口、冲突、修订和升级建议 |
| `TerminationGuard` | Mixed | 接收语义 Proposal，确定性验证完成与硬限制 |

### 5.3 Runtime Plane

| 模块 | 职责 |
|---|---|
| `RunEngine` | 驱动可重入 Runtime Tick |
| `StateMachine` | 校验 Run、Task、Attempt 和 Gate 转移 |
| `CheckpointManager` | 在语义边界保存恢复闭包 |
| `RecoveryManager` | 重建 Ready Work、处理过期 Lease 与未知调用 |
| `LeaseManager` | Claim、Epoch、Fencing 和过期 |
| `BudgetGuard` | Deadline、Token、Cost、Step、Retry、Replan 和 Revision 限制 |
| `GateManager` | 登记等待、验证响应、单次恢复 |
| `EventStore` | 保存 Append-only Domain Event |
| `OutboxDispatcher` | 可靠发布本地事务产生的通知 |

Runtime 模块必须保持确定性，不允许以 LLM 输出直接驱动状态提交。

### 5.4 Worker Plane

首期 Worker：

- `ResearchPlanner`；
- `EvidenceResearcher`；
- `Analyst`；
- `ReportWriter`；
- `ReportReviewer`。

```text
WorkerDefinition
= Role
+ Input Contract
+ Output Contract
+ Prompt / Policy Version
+ Allowed Capabilities
+ Budget
```

`WorkerExecutor` 根据 Task 构建最小充分 Context。Worker 不能直接访问 Runtime Repository，也
不能直接更新 Run State，只能提交结构化 `TaskResult` 或 Proposal。

### 5.5 Capability Plane

`CapabilityRegistry` 登记：

- Capability ID 与版本；
- Input / Output Schema；
- 权限和数据范围；
- 成本、Timeout 和并发属性；
- 可用 Adapter；
- 健康状态和降级标签。

`CapabilityRouter` 根据 Task 标签、Run Policy、可用性、预算和实现优先级选择兼容 Adapter。

统一调用语义：

```text
invoke(CapabilityRequest) → CapabilityResult
```

所有 Adapter 错误必须映射为 Orchestrator 自有 FailureCode。

### 5.6 System Port 与 Task Capability

为避免 Planner 直接获得系统基础设施权限，能力分为：

1. **Task Capability**：Memory、RAG、Web，只能由获得授权的 Worker 请求；
2. **Internal Model Capability**：由 GoalNormalizer、Planner 和 WorkerExecutor 通过命名
   Model Profile 使用，Planner 不能生成任意原始模型调用；
3. **System Port**：Repository、ArtifactStore、Clock、ID Generator、Event Publisher，只对
   Application 和 Runtime 开放。

---

## 6. 建议目录结构

```text
agents_orchestration/
├── README.md
├── pyproject.toml
├── .env.example
├── docs/
│   ├── knowledge/
│   └── specs/
├── storage/                    # gitignore
├── artifacts/                  # gitignore
├── tests/
│   ├── architecture/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   └── e2e/
└── src/agents_orchestration/
    ├── __init__.py
    ├── cli.py
    ├── config.py
    ├── application/
    │   ├── service.py
    │   ├── commands.py
    │   └── queries.py
    ├── domain/
    │   ├── models.py
    │   ├── contracts.py
    │   ├── events.py
    │   ├── failures.py
    │   └── policies.py
    ├── orchestration/
    │   ├── goal.py
    │   ├── planner.py
    │   ├── plan_validator.py
    │   ├── scheduler.py
    │   ├── reviewer.py
    │   └── termination.py
    ├── runtime/
    │   ├── engine.py
    │   ├── state_machine.py
    │   ├── checkpoint.py
    │   ├── recovery.py
    │   ├── lease.py
    │   ├── budget.py
    │   ├── gates.py
    │   └── outbox.py
    ├── workers/
    │   ├── definitions.py
    │   ├── registry.py
    │   └── executor.py
    ├── capabilities/
    │   ├── models.py
    │   ├── ports.py
    │   ├── registry.py
    │   └── router.py
    └── adapters/
        ├── memory.py
        ├── rag.py
        ├── web.py
        ├── model.py
        ├── sqlite.py
        ├── artifacts.py
        └── clock.py
```

实现时可继续拆分过大的文件，但不得跨越以上模块责任。

---

## 7. 核心领域模型

### 7.1 顶层对象

| 对象 | 含义 |
|---|---|
| `GoalSpec` | 规范化目标、范围、受众、约束和数据策略 |
| `CompletionContract` | 必要产物、证据、约束、效果和审批 |
| `PlanGraph` | 版本化 Task 定义与依赖 |
| `Run` | 一次用户目标的完整生命周期 |
| `Task` | 稳定业务工作单元 |
| `Attempt` | Task 的一次实际执行尝试 |
| `WorkerDefinition` | 角色、契约、允许能力和策略 |
| `CapabilityRequest` | 结构化能力调用请求 |
| `CapabilityResult` | 结构化结果、证据、用量和失败 |
| `Evidence` | 带来源、时间、范围和可信状态的证据 |
| `ArtifactRef` | 不可变大型产物引用 |
| `GateRequest` | 版本绑定的人工等待协议 |
| `TerminationDecision` | 完成、停止和外部效果判断 |

### 7.2 身份规则

```text
Run ID       = 一次目标生命周期，Resume 不改变
Task ID      = 稳定业务工作，Retry 不改变
Attempt ID   = 每次执行新建，不覆盖历史
Operation ID = 一次外部调用身份，用于去重和对账
```

Replan 创建新的 `Plan Version`，不覆盖旧计划。Completion Contract 的授权变更创建新版本，
不原地修改旧契约。

### 7.3 PlanGraph

`PlanGraph` 至少包含：

- Plan ID、Version、Parent Version；
- TaskSpec 集合；
- Dependency Edge；
- Required / Optional / Conditional Task；
- Worker Type；
- Required Capability；
- Input Artifact；
- Output Contract；
- Task Budget；
- Completion Criteria 覆盖；
- 生成原因和 Replan Reason。

PlanValidator 必须拒绝：

- 非法循环；
- 未注册 Worker 或 Capability；
- 超出系统上限的 Task、Depth、Fan-out；
- 无输出契约的 Task；
- Required Deliverable 无生产路径；
- 越权数据源或网络能力；
- 无法满足 Run Deadline 或预算的显然非法计划。

---

## 8. 状态机

### 8.1 Run

```text
CREATED
→ NORMALIZING_GOAL
→ PLANNING
→ RUNNING

RUNNING
⇄ WAITING_FOR_HUMAN
 / PAUSED
 / WAITING_RETRY

RUNNING
→ VERIFYING_COMPLETION
→ FINALIZING
→ COMPLETED
 / PARTIALLY_COMPLETED
 / FAILED
 / CANCELLED
 / EXHAUSTED
 / RECONCILIATION_REQUIRED
 / ESCALATED
```

停止执行、目标结果和外部效果状态必须分别记录。

### 8.2 Task

```text
PLANNED
→ PENDING
→ READY
→ RUNNING

RUNNING
→ WAITING_RETRY
 / WAITING_FOR_DEPENDENCY
 / WAITING_FOR_HUMAN
→ READY

RUNNING
→ COMPLETED
 / FAILED
 / CANCELLED
 / SUPERSEDED
 / OUTCOME_UNKNOWN
```

`SUPERSEDED` 表示 Replan 后不再属于当前有效计划。其迟到结果保留为 Observation，但不能覆盖
正式 State。

### 8.3 Attempt

```text
CREATED
→ CLAIMED
→ RUNNING
→ SUCCEEDED
 / FAILED
 / TIMED_OUT
 / CANCELLED
 / LEASE_LOST

SUCCEEDED
→ RESULT_ACCEPTED
 / RESULT_REJECTED
 / RESULT_RECONCILE
```

Attempt 成功不等于 Task 完成。结果只有在 Task、Plan Version、State Version 和 Lease Epoch
仍匹配时才可接受。

---

## 9. 数据持久化

### 9.1 SQLite 正式记录

首期至少包含以下逻辑表：

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

具体字段和索引在 OpenSpec 设计阶段细化，但必须支持 Run/Task/Attempt 身份、State Version、
Plan Version、Lease Epoch、Request ID 和 Artifact Hash。

### 9.2 Artifact Store

保存：

- 原始模型响应；
- Memory、RAG、Web 原始结果；
- 规范化 EvidenceSet；
- Analysis Artifact；
- Report Draft；
- Review Proposal；
- 最终 Markdown 与 JSON。

SQLite 只保存 `ArtifactRef`、Hash、Type、Version、Producer、Classification 和 Retention。

### 9.3 混合持久化

```text
Append-only Event  保存事实和因果
+ Current Snapshot 快速读取
+ Semantic Checkpoint 保存恢复闭包
```

同一 SQLite 事务中提交：

```text
State Version
+ Task / Attempt Transition
+ Checkpoint
+ Domain Event
+ Outbox Record
```

大型 Artifact 必须先完成不可变写入，再把 ArtifactRef 纳入事务提交。

---

## 10. 主执行流

### 10.1 Goal Normalization

输入包含：

- 用户目标；
- 可选背景；
- 允许数据源；
- 联网权限；
- 时间范围；
- 报告受众和格式；
- Run Policy Override。

`GoalNormalizer` 输出 GoalSpec 和 Completion Contract Proposal。若关键目标、受众、时间范围或
数据边界无法安全推断，触发 `GOAL_CLARIFICATION`。

### 10.2 Planning

Planner 只生成 PlanGraph Proposal，只能引用 Registry 中已注册的 Worker 和 Capability。
PlanValidator 接受后才创建 Task。

当预计成本较高、范围敏感或 Run Policy 强制时，Plan 接受前触发 `PLAN_APPROVAL`。

### 10.3 Research Fan-out

研究问题形成稳定 Task / Branch ID，并根据策略派发：

- Memory Lane：用户背景、偏好和既有决策，默认 Optional；
- RAG Lane：本地领域知识和 Citation，可由 Goal Policy 标记为 Required；
- Web Lane：当前信息，只有显式联网权限时启用。

每个 Branch 独立提交结果，不等待全局 Stop-the-world Checkpoint。

### 10.4 Evidence Join

Join 负责：

- 读取 Accepted Attempt Result；
- 按 Source Identity 去重；
- 保留来源、时间和新鲜度；
- 标记关键冲突；
- 区分 Required / Optional Branch；
- 生成 EvidenceSet 和 Sufficiency；
- 拒绝 Superseded Task 和 Late Result。

### 10.5 Analyze、Write 与 Review

```text
EvidenceSet
→ AnalysisArtifact
→ Report Draft
→ Review Proposal
```

Reviewer 输出：

```text
PASS
REVISE
RESEARCH_GAP
CONFLICT
ESCALATE
```

Reviewer 只能提出 Proposal。Runtime 根据 Contract、预算和状态选择修订、补充研究、Gate、
降级或终止。

### 10.6 Finalize

TerminationGuard 验证：

- Completion Contract；
- 必要 Artifact；
- Evidence Sufficiency；
- Required Task；
- 关键冲突；
- 未决 Attempt；
- State / Plan Version；
- Budget 和 Deadline；
- 降级披露。

通过后生成三类最终 Artifact，提交 Final Checkpoint 和结构化 Termination Decision。

---

## 11. 有界动态重规划

### 11.1 触发条件

- 发现必要证据缺口；
- 关键来源冲突；
- Required Capability 不可用；
- Plan 约束失效；
- Report Review 返回 `RESEARCH_GAP`；
- 用户通过 Gate 修改范围。

### 11.2 重规划规则

```text
Replan Proposal
→ Deterministic Validation
→ PlanGraph v(N+1)
→ Preserve unaffected work
→ Supersede invalidated tasks
→ Add focused tasks
```

Replan 不能改写已提交的 Event、Evidence 和 Artifact，只能使其对新计划失效或保留。

### 11.3 默认系统上限

首期采用以下安全默认值，允许系统管理员收紧或在系统上限内调整：

| 限制 | 默认值 |
|---|---:|
| 最大 Task 数 | 32 |
| 最大 Plan Depth | 4 |
| 最大并发 Task | 4 |
| 每 Task 最大 Attempt | 3 |
| 最大 Replan 次数 | 2 |
| 最大 Report Revision 次数 | 2 |
| 默认 Run Deadline | 30 分钟 |

Run Policy 不能把这些限制扩大到系统配置之外。

---

## 12. Runtime Tick 与 Context

### 12.1 可重入 Tick

```text
load Run + State Version
→ process Event / Gate Response
→ expire Lease / Timer / Deadline
→ accept or reject Attempt Result
→ recompute Ready Tasks
→ apply Policy / Budget / Concurrency
→ claim and dispatch bounded work
→ evaluate Replan / Completion / Pause / Termination
→ commit State + Event + Checkpoint + Outbox
```

每次 Tick 都可以从 SQLite 正式状态重新计算，不依赖旧进程调用栈。

### 12.2 Context Projection

Worker 只接收：

```text
Goal 摘要
+ 当前 Task Contract
+ 必要上游 Artifact
+ 允许的 Evidence
+ Worker Policy
+ Budget
+ Output Schema
```

不向 Worker 注入整个 Run 历史。Memory、RAG 和 Web 原始内容保存在 Artifact Store，Prompt
只注入当前 Task 需要的片段和引用。

---

## 13. 可靠性设计

### 13.1 失败分类

统一 Failure Category：

| 类别 | 典型情况 | 默认策略 |
|---|---|---|
| `TRANSIENT` | 429、短暂网络失败 | 有界 Retry + Backoff |
| `PERMANENT` | 参数、Schema、鉴权错误 | 不重试，Replan 或 Fail |
| `POLICY` | Capability、Scope、网络被拒绝 | 不重试，调整计划或 Gate |
| `UNKNOWN` | Timeout 后结果不明 | 查询、对账或标记 Unknown |

Adapter 必须返回：

- FailureCode；
- Retryable；
- Retry-After；
- Operation ID；
- Outcome Certainty；
- Safe Diagnostic。

### 13.2 Retry

Retry 新建 Attempt。失败分类、Retry Budget、Backoff Timer 和 Deadline 共同持久化。重试不重置
Run Budget。

### 13.3 Checkpoint

强制语义边界：

- Plan 接受后；
- Branch Result 接受后；
- 进入 Gate 前；
- Retry Timer 建立后；
- Replan 提交后；
- 最终状态提交前。

### 13.4 Recovery

进程恢复：

```text
加载最新正式 State / Checkpoint
→ 使过期 Lease 失效
→ 拒绝旧 Attempt Result
→ 检查未知 Capability Call
→ 重建 Ready Work
→ 继续 Tick
```

### 13.5 Late Result

结果提交必须验证：

- Active Attempt；
- Lease Epoch；
- Plan Version；
- Expected State Version；
- Task 未被 Supersede；
- Run 未进入禁止提交的终态。

不满足时默认 `RESULT_REJECTED`，但保留为 Observation。

---

## 14. Human-in-the-loop

### 14.1 Gate 状态机

```text
RUNNING
→ GATE_REQUESTED
→ WAITING_FOR_HUMAN
→ RESPONSE_RECEIVED
→ VALIDATING_RESPONSE
→ RESUME_REQUESTED
→ RUNNING

或：
EXPIRED / REJECTED / CANCELLED / ESCALATED
```

### 14.2 GateRequest

必须绑定：

- Gate ID；
- Run / Task；
- Gate Type；
- Allowed Actor / Role；
- Scope；
- State Version；
- Plan Version；
- Artifact Hash；
- Created / Expires At；
- Allowed Response Schema；
- Single-use Consumption Status。

重复 Response Event 只能消费一次；旧版本审批不能作用于新 Artifact 或新 Plan。

### 14.3 默认触发规则

- Goal 的必要字段无法安全推断：`GOAL_CLARIFICATION`；
- 计划超出 Run Policy 的自动批准阈值：`PLAN_APPROVAL`；
- Required Evidence 存在无法自动解决的关键冲突：`CONFLICT_RESOLUTION`；
- Run Policy 显式要求最终人工确认：`FINAL_REVIEW`。

---

## 15. 安全设计

### 15.1 权限链

```text
Worker Proposal
→ Worker Capability Allowlist
→ Run Policy
→ System Policy
→ Adapter Enforcement
```

Planner 不能扩大 Worker 权限，Run Policy 不能扩大 System Policy。

### 15.2 不可信内容

Memory、RAG 和 Web 内容全部视为 Evidence，而不是 Control Instruction：

```text
Untrusted Content
→ Normalize + Source Label + Trust Metadata
→ Evidence Context
```

外部文本中的工具调用、忽略规则、读取 Secret 等指令不得转换成 Plan、Policy 或 Capability
权限。

### 15.3 Secret

- Secret 只在 Adapter 调用边界从环境或 Secret Provider 获取；
- Event、State、Checkpoint、Artifact 和日志只保存 Secret Reference；
- CLI 不回显 Secret；
- Core 不读取同级项目 `.env`；
- 模型 Prompt 不包含 Secret。

### 15.4 首期副作用边界

报告可以提出业务动作建议，但 Runtime 没有发布、发送、支付、部署、代码执行和文件修改
Capability。首期安全性不能依赖 Prompt 拒绝，而由 Registry 中不存在写 Capability 强制保证。

---

## 16. 降级策略

| 失败能力 | 允许降级 | 禁止行为 |
|---|---|---|
| Memory | 标记缺少个性化背景后继续 | 跨用户召回或伪造偏好 |
| RAG | Policy 允许时使用 Web，并披露本地知识不可用 | 假装引用本地知识 |
| Web | 使用 RAG / Memory，并披露时效限制 | 把旧数据描述成当前事实 |
| Model | 有界 Retry 或切换兼容 Model Profile | 切换到违反隐私或策略的模型 |
| Evidence | Partial、Unknown、Gate 或 Fail | 证据不足却提交 Succeeded |

降级必须进入 `CapabilityResult`、Domain Event、Run Summary 和最终报告限制说明。

---

## 17. 配置

### 17.1 配置分层

| 层 | 内容 |
|---|---|
| App Settings | SQLite、Artifact、日志、默认 Model Profile |
| Registry | Worker / Capability 定义、Adapter 绑定和版本 |
| Run Policy | 网络、域、预算、并发、Gate、降级 |
| Secrets | Adapter API Key，只从环境或 Secret Provider 读取 |

优先级：

```text
Run Policy（只能收紧或在系统允许范围内调整）
→ App Settings
→ 安全默认值
```

### 17.2 建议技术基础

与现有子项目保持一致：

- Python 3.12；
- Pydantic v2 / pydantic-settings；
- Typer + Rich；
- pytest / pytest-asyncio / pytest-cov；
- Ruff；
- Hatchling；
- OpenAI-compatible Model Adapter。

Runtime 内部采用 Async Capability Port，以支持并行研究；现有同步 Memory/RAG API 由 Adapter
受控包装。

---

## 18. CLI 与 Python API

### 18.1 执行模型

默认 `Run Until Blocked`：

- `run start` 创建 Run，并在当前进程中持续推进该 Run；
- `--follow` 只控制是否持续展示 Event，不改变执行语义；
- `--create-only` 只创建 `CREATED` Run，交给后续 Runtime Driver 处理；
- 到达终态或 Human Gate 后返回；
- 进程退出不丢状态；
- `run resume` 提交恢复控制事件，并默认持续推进该 Run，直到再次阻塞。

面向开发、测试和本地运维，提供独立 Runtime Driver：

- `runtime tick RUN_ID`：只对指定 Run 执行一个有界 Runtime Tick，然后退出；
- `runtime watch --run RUN_ID`：持续推进指定 Run，直到终态、Gate、Pause 或进程停止；
- `runtime watch`：轮询 SQLite 中所有可运行 Run；
- `--poll-interval`：配置 Watch 空闲轮询间隔；
- 首期只支持一个持续 Watch 进程，不提供守护进程管理或多实例分布式调度。

```text
run start REQUEST.json
≈ create Run + runtime watch --run RUN_ID

run start REQUEST.json --create-only
→ 只持久化 Run，之后由 runtime tick / watch 推进
```

`run start` 和 `run resume` 是面向用户的业务命令；`runtime tick` 和 `runtime watch` 是执行控制
命令。`--run` 只限定调度范围，不负责改变 Pause、Cancel 或 Gate 状态。

### 18.2 CLI

```text
agents-orchestrator run start REQUEST.json [--follow] [--create-only]
agents-orchestrator run show RUN_ID [--json]
agents-orchestrator run watch RUN_ID
agents-orchestrator run pause RUN_ID
agents-orchestrator run resume RUN_ID
agents-orchestrator run cancel RUN_ID

agents-orchestrator gate list [--run RUN_ID]
agents-orchestrator gate respond GATE_ID RESPONSE.json

agents-orchestrator artifact list RUN_ID
agents-orchestrator artifact export RUN_ID --output ./result

agents-orchestrator capability list
agents-orchestrator capability doctor

agents-orchestrator runtime tick RUN_ID
agents-orchestrator runtime watch [--run RUN_ID] [--poll-interval SECONDS]
```

CLI 只做参数适配和展示，所有行为复用 `OrchestrationService`。

### 18.3 Python Service API

```text
start_run(command)
run_until_blocked(run_id)
get_run(run_id)
pause_run(run_id, expected_version)
resume_run(run_id, event)
cancel_run(run_id, reason)
respond_gate(gate_id, response, expected_version)
export_artifacts(run_id, target_dir)
```

变更命令携带 Request ID 或 Expected Version，支持幂等与并发控制。

---

## 19. 错误模型

| 错误 | 含义 |
|---|---|
| `ValidationError` | Goal、Plan、Schema 或 State Transition 不合法 |
| `PolicyDenied` | Capability、Scope、网络或预算策略拒绝 |
| `CapabilityFailure` | 统一 Adapter Failure |
| `ConcurrencyConflict` | State Version、Lease Epoch 或 Gate Consumption 冲突 |
| `RecoveryRequired` | 未知调用、损坏 Checkpoint 或版本不兼容 |
| `TerminalRunError` | 不可恢复失败和结构化终止原因 |

错误需要持久化为 Event 或 Attempt Outcome。CLI 映射为稳定 Exit Code 和安全诊断，不打印
Secret、原始凭据和未脱敏 Prompt。

---

## 20. 可观测性

首期提供：

- JSONL 结构化日志；
- SQLite Domain Event 查询；
- Run / Task / Attempt / Operation Trace Correlation；
- Model Token、成本、延迟、Retry 和 Degradation Usage Ledger；
- CLI `run watch`。

首期不建设独立 Trace 平台，不把 Trace 当作正式 State 或 Evaluation。

---

## 21. 测试设计

### 21.1 Unit / Property

- Run、Task、Attempt 和 Gate 状态机；
- Plan DAG 与依赖校验；
- Budget、Deadline、Retry 和 Replan 上限；
- Required / Optional / Quorum 聚合；
- Late Result 和 State Version 条件提交；
- Loop、No Progress 和 Completion 判断；
- Context Projection；
- Failure Mapping。

### 21.2 Architecture Test

- Domain 不导入 Infrastructure；
- Core 不导入 `agents_memory`、`agents_rag` 或第三方 Provider；
- 只有对应 Adapter 可以导入 sibling 项目公开 API；
- sibling 子项目不依赖 Orchestrator；
- CLI 不复制 Application / Domain 逻辑。

### 21.3 Contract Test

每个 Capability Adapter 使用统一测试套件验证：

- Schema；
- Timeout；
- Cancellation；
- FailureCode；
- Source / Citation；
- Usage；
- Degradation；
- Secret Redaction。

### 21.4 Integration

- SQLite Repository；
- Artifact Store；
- State + Event + Checkpoint + Outbox 原子提交；
- Lease Claim 和 Fencing；
- Gate 单次消费；
- Memory/RAG 同步 API 的 Async Adapter。

### 21.5 E2E with Fakes

默认测试使用 Fake Model、Memory、RAG 和 Web，不访问网络，覆盖完整研究 Run、并行 Join、
Replan、Gate、恢复、降级和最终 Artifact。

### 21.6 Failure Injection

必须覆盖：

1. Plan 提交后、Task 派发前崩溃；
2. Branch 完成后、Join 前崩溃；
3. 旧 Attempt 在 Lease 失效后返回；
4. Gate Response 重复投递；
5. Replan 与 Late Result 竞争；
6. 各 Capability 单独失败；
7. SQLite 事务失败；
8. Artifact 已写入但事务提交失败；
9. Deadline 在 Worker 执行期间到达；
10. Final Verification 期间 State Version 改变。

### 21.7 Live Smoke

真实模型、Memory、RAG 和 Web 测试必须显式启用，不属于默认测试套件，不得在 CI 中自动消耗
外部额度。

---

## 22. 首期验收清单

1. Goal JSON 能创建 Durable Run；
2. GoalSpec 和 Completion Contract 可查询、版本化；
3. PlanGraph Proposal 必须经过 PlanValidator；
4. Task、Attempt 和 Plan Version 历史完整保留；
5. Memory、RAG、Web 能通过 Registry 切换 Fake 或真实 Adapter；
6. 研究分支并行执行并独立提交；
7. Evidence Join 保留来源、冲突、充分性和降级；
8. Reviewer 可触发有界 Revision 或 Replan；
9. 四类 Gate 可以按 Policy 触发；
10. 重复 Gate Response 不会重复恢复；
11. 重启后可以从 Checkpoint 恢复；
12. Lease 失效后的 Late Result 不覆盖正式 State；
13. 能生成 `report.md`、`report.json` 和 `run-summary.json`；
14. 降级和未解决问题在最终交付物中披露；
15. 首期没有真实写副作用路径；
16. 默认测试不访问真实网络；
17. 关键状态机、恢复和故障注入测试稳定通过。

---

## 23. 后续扩展边界

以下扩展不进入首期，但当前 Port 和领域模型应允许未来增加：

- FastAPI 和 Web 控制台；
- HTTP / Queue Capability Adapter；
- 分布式 Worker 和 Workflow Runtime Adapter；
- 版本化 Worker Plugin；
- 审批后发布、邮件等 Effect-safe Capability；
- PDF / Word / Web Artifact Renderer；
- 多租户、ACL 和更细的数据分类；
- 独立 Observability Backend；
- 统一 Agent 评测与回归平台。

增加真实副作用前，必须另行设计 Operation Identity、Idempotency、Effect Ledger、Compensation、
Reconciliation 和 Approval Scope，不能直接复用首期只读调用语义。

---

## 24. 架构不变量

1. LLM 输出永远是 Proposal；
2. 正式状态只能由确定性 StateMachine 和事务提交改变；
3. Retry、Resume 创建新 Attempt，不覆盖历史；
4. Replan 创建新 Plan Version，不改写旧计划和已提交事实；
5. Worker 不直接访问 Runtime Repository；
6. Planner 不能扩大 Worker Capability 权限；
7. Capability 只能通过 Registry、Router 和 Adapter 调用；
8. Core 不依赖外部 Provider 和 sibling 实现；
9. SQLite 是 Orchestrator 正式状态真相源；
10. Artifact 内容不可变，SQLite 只保存带 Hash 的引用；
11. Ready Work 必须可从正式 State 重算；
12. Gate Response 绑定版本并最多消费一次；
13. Late Result 默认不能覆盖当前状态；
14. Evidence 不等于 Instruction；
15. Secret 不进入 Prompt、State、Event、Checkpoint 和 Artifact；
16. Degradation 必须显式记录并向最终用户披露；
17. 终止执行不等于任务成功；
18. Required Evidence 不足时不能提交干净的 `SUCCEEDED`；
19. 首期通过 Capability Registry 物理阻止写副作用；
20. 第三方框架只能作为 Adapter，不能成为领域模型本身。
