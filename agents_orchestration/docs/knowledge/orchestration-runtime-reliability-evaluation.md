# Agent Orchestration 知识点：运行时、可靠性与评测（Runtime, Reliability and Evaluation）

> **文档定位**：本文聚焦 Agent 编排如何从一次模型演示演进为可持久、可恢复、可治理、可观测和可评测的运行系统。
>
> **与其他知识文档的区别**：
> - [编排基础](agent-orchestration-foundations.md) 建立编排定义与系统分层
> - [编排模式](orchestration-patterns.md) 讨论执行拓扑和控制权流动
> - [状态与上下文](orchestration-state-and-context.md) 讨论状态、上下文、消息、产物和所有权
> - 本文讨论这些模式和状态如何在失败、并发、长运行与副作用条件下可靠运转，并如何建立评测闭环
>
> **深入进度**：✅ §0 核心认知已建立；✅ §1 Runtime 职责已深入至统一身份、执行循环与运行时不变量；⏸️ §2 当前已深入同步、后台、长运行和流式交付边界；✅ §7 已深入时间预算、Lease、Cancellation 与 Late Result；⏸️ §8 当前已完成统一语义、§8.1 检查点内容和 §8.2 检查点时机；🔄 §10 已建立统一模型并完成 §10.1 任务完成条件，下一步可深入 §10.2 证据充分性；主线后续回到 §3 失败模型。
>
> **更新日期**：2026-07-27

---

## 0. 核心认知

### 0.1 成功执行一次不等于可靠编排

一次演示成功，只能证明某条路径在某次采样和某个环境中能够运行，不能证明系统可以从失败中恢复、阻止重复副作用、保持并发状态一致，或让长运行任务进入有界终态。

可靠性不是“系统从不失败”，而是：

> 在模型失败、工具失败、网络中断、重复投递、进程崩溃、并发竞争和人工延迟存在时，系统仍能维护状态、安全和业务效果不变量，并最终进入可解释的终态。

```text
Reliability
= Failure Tolerance
+ State Integrity
+ Effect Safety
+ Bounded Progress
+ Recoverability
+ Explainability
```

### 0.2 Agent 的不确定性不能扩散为系统状态的不确定性

Agent 可以产生概率性建议：

```yaml
proposal:
  next_agent: finance-agent
  confidence: 0.78
```

正式状态转换必须确定：

```yaml
event:
  type: TASK_ASSIGNED
  task_id: task-12
  owner: finance-agent
  state_version: 18
```

中间需要：

```text
LLM Proposal
    ↓
Schema Validation
    ↓
Policy Validation
    ↓
Capability / Permission Check
    ↓
Deterministic State Transition
```

State 可以记录置信度和未决冲突，但不能处于“也许已经退款”或“大概已经 Handoff”的模糊状态。

### 0.3 Exactly-once 通常是业务效果目标，不是投递事实

消息可能重复投递，Worker 也可能在执行完成但提交确认前崩溃。因此后续追求的通常不是传输层天然只投递一次，而是：

```text
At-least-once Delivery
        +
Idempotent / Deduplicated Execution
        +
Verified Commit
        ↓
Exactly-once Effect
```

具体语义在 §5 深入。

### 0.4 可恢复执行依赖显式状态和提交边界

调度、暂停和恢复必须以正式 State 为依据，而不能依赖当前进程对象、存活调用栈、模型上下文、对话历史或尚未导出的 Trace。

如果 Orchestrator 崩溃，Runtime 应能从持久状态重新确定已完成和 Ready 的 Task、失效的 Attempt、已发生的副作用、等待中的 HumanRequest，以及下一条合法状态转换。

### 0.5 Trace 不等于 Evaluation

```text
Trace      = 实际发生了什么
Evaluation = 发生得是否正确、充分、安全和高效
```

Trace 是评测证据，不是评测结论。Trace 完整只能说明运行证据充分，不能说明执行正确。

### 0.6 最终答案质量不能掩盖错误执行路径

Agent 可能给出看似正确的最终答案，却在过程中读取无权访问的数据、调用错误工具、重复副作用、绕过审批或执行大量无效循环。

因此编排评测既要看 Outcome，也要看 Trajectory、State Transition、Tool Use、Safety 和 Reliability。

## 1. Orchestration Runtime 的职责

### 1.1 编排语义与运行时机制

Orchestration Runtime 是：

> 承载编排实例运行，并强制执行其调度、状态转换、消息投递、时间控制、资源限制和生命周期语义的系统机制。

它不是 Agent 的智能，而是 Agent 智能得以可靠运行的执行基础：

```text
Agent / Planner / Supervisor
决定“想做什么”
          ↓
Runtime
决定“何时、以什么身份、在什么约束下执行”
          ↓
Worker / Tool / External System
产生真实结果
```

编排模式定义协作语义，Runtime 负责兑现这些语义：

| 编排语义 | Runtime 机制 |
|---|---|
| 委派给 Worker | 创建 Task、投递工作、记录 Owner |
| 并行执行子任务 | Fan-out、并发限制、结果聚合 |
| 等待人工审批 | 持久化状态、Durable Timer、Pause |
| 工具失败后重试 | Attempt、Retry Policy、Backoff |
| 转移给 Specialist | Ownership Transition、Context Projection |
| 到达预算后终止 | Budget Accounting、Cancellation |
| Planner 修改计划 | Plan Version、Invalidation、State Commit |
| 恢复长运行任务 | Checkpoint、Replay、Idempotency |

Prompt 中写“现在由财务 Agent 接管”只是一段语言输出。可靠 Handoff 还必须正式记录新 Owner、停止旧 Owner 写入、移交待处理任务，并按新权限重建 Context。

#### 1.1.1 核心执行对象

推荐采用以下身份层级：

```text
Orchestration Definition
          ↓ instantiate
Run
 ├── Task
 │    ├── Attempt 1
 │    │     ├── Step / Action
 │    │     └── Observation
 │    └── Attempt 2
 │          └── ...
 ├── Task
 └── ...
```

**Orchestration Definition** 描述可复用的节点、边、状态 Schema、Agent、工具、策略、超时、预算和版本。

**Run** 是一次端到端编排实例：

```yaml
run_id: run-001
definition_id: refund-workflow
definition_version: 3
status: RUNNING
started_at: "..."
```

**Task** 是可独立调度、委派和判断完成的工作单元：

```yaml
task_id: task-12
run_id: run-001
owner: finance-agent
status: READY
```

**Attempt** 是 Task 的一次具体执行尝试：

```yaml
attempt_id: attempt-2
task_id: task-12
attempt_number: 2
status: RUNNING
```

Retry 应创建新 Attempt，而不是覆盖第一次失败：

```text
同一个 Task
  ├── Attempt 1：TIMEOUT
  └── Attempt 2：SUCCEEDED
```

**Step / Action** 是 Attempt 内的一次模型调用、工具调用或状态转换。涉及外部副作用时，还需要独立操作身份：

```yaml
operation_id: refund-order-1024
idempotency_key: run-001:task-12:refund
```

**Observation** 是模型、工具或外部系统产生的反馈。它是状态变更的证据，但不自动等于正式 State。

#### 1.1.2 最小执行循环

```text
读取正式 State
      ↓
计算 Ready Tasks
      ↓
应用权限、预算和并发策略
      ↓
创建 Attempt 并投递
      ↓
Worker / Agent / Tool 执行
      ↓
产生 Observation / Error
      ↓
验证结果并提交 State Transition
      ↓
生成后续 Task / Timer / Event
      ↓
继续、暂停、完成或失败
```

Ready Work 必须能从正式 State 重新计算，而不能只存在于调度进程内存中。

### 1.2 调度

调度器判断哪些 Task 已经 Ready，并检查前置依赖、权限、能力、预算、并发容量、Deadline、Pause、Cancellation 和 State Version。

调度器决定工作何时以及在哪里执行，但不应偷偷承担 Planner 的业务规划职责。

### 1.3 事件与消息传递

Runtime 负责传递 Command、Task Assignment、Observation、Event、Cancellation 和 Human Response，但必须区分：

```text
消息成功投递
    ≠
Worker 成功执行
    ≠
业务效果成功提交
```

### 1.4 状态持久化

正式状态不能只存在于进程内对象、Prompt、对话历史、Worker 内存或 Trace 中。Runtime 需要提供明确的持久化与提交边界，以支持崩溃恢复、版本检查、并发控制以及执行对象间的身份关联。

### 1.5 定时器与等待

延迟重试、人工审批、Deadline、租约过期和超时升级不能依赖进程内 `sleep`。Runtime 应记录可持久化 Timer：

```yaml
timer_id: timer-9
fire_at: "..."
reason: RETRY_BACKOFF
related_task: task-12
```

暂停和等待是正式状态转换，而不是长期占用一个线程。

### 1.6 检查点与恢复

Checkpoint 需要保存恢复所需的正式状态，而不只是聊天消息。恢复时应能够判断哪些步骤已经提交、哪些计算可重算、哪些副作用不可重复、哪个 Attempt 仍有效，以及 Definition 版本是否兼容。

Checkpoint 的具体内容、时机和版本迁移在 §8 深入。

### 1.7 资源与并发管理

Runtime 应限制全局并发、用户和租户配额、Agent 和工具并发、Fan-out 数量，以及 Token、时间和货币预算。

### 1.8 执行隔离

不同 Run、用户和租户之间必须隔离 State、Context、Secret、工具权限、Artifact、Trace、预算和配额。隔离可以通过进程、容器、沙箱、Actor 或逻辑作用域实现，但安全语义不能只依赖 Prompt。

### 1.9 生命周期管理

Runtime 管理 Run 的正式生命周期：

```text
CREATED
→ RUNNING
→ WAITING / PAUSED
→ RUNNING
→ COMPLETED / FAILED / CANCELLED
```

它还需要协调 Task、Attempt、Timer、Lease、Approval 和 Artifact 的生命周期。

### 1.10 Runtime 不负责的决策

Runtime 不应自行理解或修改用户 Goal、生成业务计划、决定领域事实、判断自然语言产物质量，或因基础设施失败而静默降低目标。

Runtime 可以承载 Planner、Evaluator 和 Policy Engine，但自身职责是：

```text
承载并强制执行决策
而不是偷偷创造新的业务决策
```

### 1.11 可观测证据输出

Runtime 应产生结构化 Event、Trace 和 Span，用于调试、回放、故障归因、成本统计、路径评测和安全审计。

Trace 应与 Run、Task、Attempt、Action、State Version 和 Artifact Identity 可关联，同时遵循敏感数据脱敏策略。

### 1.12 Runtime 基本不变量

无论采用本地循环、队列、Actor 还是 Workflow Runtime，都应尽量保证：

1. 每个 Run、Task、Attempt 和副作用操作都有稳定身份；
2. Retry 创建新 Attempt，不覆盖历史失败；
3. 正式状态转换经过校验、版本控制并可追踪；
4. Ready Work 能从正式 State 重新计算；
5. Pause 不依赖存活的进程和调用栈；
6. Timer、Deadline 和人工等待可以持久化；
7. 外部副作用拥有操作身份和幂等策略；
8. 重复、迟到或失效结果不能直接覆盖当前 State；
9. 权限、并发、预算和终止限制由 Runtime 强制执行；
10. Resume 从正式 State 开始，而不是依赖模型上下文；
11. Trace 与 State 使用可关联身份；
12. Runtime 崩溃不能让已提交事实变回“未发生”。

### 1.13 当前阶段结论

1. Runtime 是编排语义的执行承载，不是 Agent 智能本身；
2. 编排模式定义如何协作，Runtime 保证这些语义在故障条件下成立；
3. Run、Task、Attempt、Step 和副作用 Operation 必须使用不同身份；
4. Retry 应创建新 Attempt；
5. 调度和恢复必须基于正式 State；
6. LLM 输出是 Proposal，不能直接成为正式状态转换；
7. Runtime 必须强制权限、并发、预算和终止约束；
8. Ready Work、Timer 和等待状态都应可持久恢复；
9. Runtime 产生可观测证据，但 Trace 本身不是 Evaluation；
10. 后续可靠性机制建立在统一执行身份和运行时不变量之上。

## 2. 执行模型

### 2.1 同步与异步

同步和异步只表示调用方如何等待结果：

```text
run_sync()
run_async()
```

它不说明任务是否持久化、是否后台运行或是否支持失败恢复。

### 2.2 请求内执行与后台执行

执行承载方式可以是：

```text
请求内执行
后台 Worker
分布式 Workflow Runtime
```

请求内执行依赖调用进程和连接持续存活；后台执行通常先创建 Run，再通过轮询、Event、Webhook 或流式通道观察状态。

```text
支持 async API
      ≠
支持持久化后台执行

返回 Run ID
      ≠
已经具备可靠 Runtime
```

### 2.3 短任务与长运行任务

短任务适合请求内执行的条件包括：

- 执行时间短；
- 不等待人工；
- 没有长时间外部依赖；
- 失败后可安全整体重跑；
- 副作用有限；
- 调用方可持续保持连接。

以下任一条件出现时，应考虑 Durable Runtime：

- 等待人工审批；
- 跨小时或跨天；
- 包含多个外部系统；
- 存在副作用；
- 需要暂停和恢复；
- 需要后台执行；
- 需要跨进程或跨机器；
- 不能安全整体重跑；
- 必须持续跟踪 Deadline、Budget 和 Ownership。

长运行不是单纯把 Timeout 设置得更长，而是从保持调用栈等待转为：

```text
持久化状态 + Durable Timer + 事件驱动恢复
```

### 2.4 Push 与 Pull

### 2.5 队列、Actor 与 Workflow Runtime

### 2.6 本地进程与分布式执行

本地与分布式执行可以采用相同编排语义，但故障和一致性边界不同：

| 维度 | 本地进程 | 分布式执行 |
|---|---|---|
| 状态访问 | 低延迟，容易误用进程内状态 | 必须显式持久化和版本控制 |
| 消息 | 常为函数调用 | 可能重复、乱序、迟到 |
| 故障 | 进程级 | Worker、网络、分区等多层故障 |
| 并发 | 单机资源范围 | 跨 Worker、租户和区域治理 |
| 部署复杂度 | 低 | 高 |

不应为了“架构先进”默认分布式。只有长运行、隔离、吞吐、弹性或跨机器能力确有需求时才增加该复杂度。

### 2.7 流式事件与最终结果

结果交付方式包括：

```text
一次性最终结果
流式 Token
流式 Event
轮询状态
Webhook
```

Streaming 只是调用方观察进展的方式：

```text
支持 streaming
      ≠
支持失败恢复
```

流式 Token 也不能作为正式状态 Event；状态变更需要结构化、可验证和可重放。

### 2.8 可重入执行

## 3. 失败模型

### 3.1 模型失败

### 3.2 工具失败

### 3.3 网络与基础设施失败

### 3.4 超时与慢执行

### 3.5 Worker 崩溃

### 3.6 Orchestrator 崩溃

### 3.7 部分成功

### 3.8 状态冲突

### 3.9 逻辑失败与质量失败

### 3.10 不可恢复失败

## 4. Retry

### 4.1 Retryable 与 Non-retryable

### 4.2 重试预算

### 4.3 指数退避与抖动

### 4.4 同一步骤重试与策略调整

### 4.5 模型重采样不是通用恢复

### 4.6 工具参数修复

### 4.7 更换模型、工具或 Agent

### 4.8 Retry Storm

### 4.9 重试可观测性

## 5. Idempotency 与副作用

### 5.1 幂等的层次

### 5.2 Task、Attempt 与操作身份

### 5.3 Idempotency Key

### 5.4 读操作与写操作

### 5.5 副作用前检查

### 5.6 副作用结果持久化

### 5.7 重复投递与重复执行

### 5.8 Exactly-once Effect

### 5.9 不可幂等外部系统

## 6. 提交、补偿与事务边界

### 6.1 Propose、Validate、Commit

### 6.2 模型建议与系统提交分离

### 6.3 Saga 与补偿动作

### 6.4 可补偿与不可补偿副作用

### 6.5 两阶段动作

### 6.6 人工审批 Gate

### 6.7 部分提交后的恢复

### 6.8 补偿失败

## 7. Timeout、Deadline 与 Cancellation

### 7.1 单步骤 Timeout

本节需要先区分：

| 概念 | 回答的问题 |
|---|---|
| Timeout | 某次操作最多允许运行多久？ |
| Deadline | 整个任务最晚什么时候必须结束？ |
| Idle Timeout | 多久没有可验证进展就认为卡住？ |
| Heartbeat | 执行者是否仍存活或仍有进展？ |
| Lease | 当前执行者的临时所有权是否仍有效？ |
| Cancellation | 系统是否已经要求停止后续工作？ |

Timeout 通常针对某次 Attempt 或外部调用：

```yaml
attempt_timeout: 60s
tool_call_timeout: 10s
idle_timeout: 20s
```

#### 7.1.1 Run Timeout 与 Idle Timeout

Run Timeout 从 Attempt 开始持续计时，无论是否产生输出，到时都触发。Idle Timeout 只有在限定时间内没有可验证进展时触发，合法 Heartbeat 或 Progress Signal 可以重置 Idle Clock。

Heartbeat 可以刷新 Idle Timeout，但不能延长硬 Run Timeout，更不能延长端到端 Deadline。

#### 7.1.2 Timeout 不等于动作失败

Timeout 只表示观察方没有在限定时间内得到可确认结果。它不证明 Worker 已停止、外部系统没有执行、副作用未发生或结果永远不会到达。

```text
Timeout
    ≠
Operation Failed
```

对于副作用操作，Timeout 后更合理的状态可能是：

```text
OUTCOME_UNKNOWN
RECONCILIATION_REQUIRED
```

而不是立即标记 `FAILED` 并重试。

#### 7.1.3 Timeout 类型

至少应区分：

```text
CALL_TIMEOUT
ATTEMPT_RUN_TIMEOUT
ATTEMPT_IDLE_TIMEOUT
TASK_DEADLINE_EXCEEDED
RUN_DEADLINE_EXCEEDED
HUMAN_RESPONSE_TIMEOUT
LEASE_EXPIRED
```

### 7.2 端到端 Deadline

Deadline 是绝对时间点，表示 Run 或 Task 最晚必须在何时进入允许的终态；Timeout 是相对时长，表示一次操作最多可以运行多久。两者不能互相替代。

父任务派生子任务时，应传播剩余时间预算，而不是让子任务重新获得完整时长：

```text
effective_child_deadline
= min(parent_deadline, now + child_max_duration)
```

子任务还应为结果提交、状态落盘、资源清理和必要补偿预留时间。Retry 创建新 Attempt，但继续消耗同一个 Task / Run Deadline 的剩余预算，不能借重试不断续期。

```text
Tool Call Timeout
≤ Attempt Timeout
≤ Task Remaining Deadline
≤ Run Remaining Deadline
```

Deadline 到达时，Runtime 应停止派发新工作并发出取消请求；已经发出的副作用操作则进入确认、对账或补偿流程，而不是被假定为自动撤销。

### 7.3 Heartbeat

Heartbeat 是执行者周期性提交的存活或进展证据，可包含：

```yaml
worker_id: worker-3
attempt_id: attempt-17
sequence: 42
emitted_at: "..."
progress_marker: "tool_call_started"
checkpoint_ref: checkpoint-8
```

需要区分两类信号：

- **Liveness Heartbeat**：证明 Worker 仍能与 Runtime 通信；
- **Progress Heartbeat**：证明 Attempt 的业务进度发生了可验证变化。

Heartbeat 不能证明结果正确、任务完成、没有重复执行、Lease 仍有效或业务一定有进展。对于持续输出 Token 的模型调用，普通输出也不应默认等价于业务进展；可以显式转换为 Heartbeat，但只能刷新 Idle Timeout，不能延长硬 Timeout 或 Deadline。

### 7.4 租约过期

Lease 是 Runtime 授予 Worker 的临时执行权：

```yaml
lease:
  holder: worker-3
  epoch: 9
  expires_at: "..."
```

Heartbeat 可以作为续租依据，但 Heartbeat 与 Lease 不是同一概念：前者是信号，后者是带期限和所有权语义的授权。Lease 过期只表示 Runtime 不再承认该 Worker 的执行权，并不证明旧 Worker 已经停止。

网络分区时，旧 Worker A 可能仍在运行，而 Runtime 已将任务重新分配给 Worker B。为避免两者都提交结果，Lease 应携带单调递增的 `epoch` 或 Fencing Token；持久化层只接受当前 epoch 的条件提交，拒绝旧持有者的写入。

### 7.5 主动取消

Cancellation 不是瞬时事件，而是一项需要持久化、传播和确认的控制请求：

```text
RUNNING
→ CANCEL_REQUESTED
→ CANCELLING
→ CANCELLED
```

若外部副作用是否发生尚不确定，终态应表达这种不确定性，例如：

```text
CANCELLED_WITH_PENDING_EFFECTS
RECONCILIATION_REQUIRED
```

取消记录至少应保存 `requested_at`、`requested_by`、`reason`、`scope`、`propagation_policy` 和当前 State Version。

取消通常是协作式的：Runtime 发出 Cancellation Token，Worker 在模型调用前后、工具调用边界、循环迭代、等待点和提交前检查它。Soft Cancel 允许安全清理和状态落盘；Hard Cancel 可以终止进程或容器，但仍不能撤销已经到达外部系统的副作用。

### 7.6 级联取消

父 Run 或 Task 被取消时，默认应向尚未进入终态的子任务级联取消，但不是所有下游工作都应停止：

- 共享缓存填充可以按策略继续；
- 审计日志写入通常应继续；
- 补偿流程可能因取消而启动；
- 对账流程应继续确认未知副作用；
- Detached Task 只有在定义阶段显式声明后才可脱离父级生命周期。

```yaml
cancel_scope:
  target: task-12
  cascade: true
  include_detached: false
  preserve:
    - audit
    - compensation
    - reconciliation
```

级联取消传播的是“停止兴趣”，不是对所有已发生业务效果执行回滚。

### 7.7 无法取消的外部动作

邮件发送、支付扣款、部署发布等外部动作一旦被系统接受，通常无法靠本地 Cancellation Token 取消。收到取消请求后，Runtime 应：

1. 停止发起新的外部动作；
2. 使用稳定的 `operation_id` 查询外部系统状态；
3. 通过查询、Webhook 或对账确认最终效果；
4. 在支持时执行补偿，否则告警或转人工；
5. 在结果明确前保持 `OUTCOME_UNKNOWN` 或 `RECONCILIATION_REQUIRED`。

```text
Cancel Interest
    ≠
Undo External Effect
```

因此，能否取消计算与能否撤销业务效果必须分开建模。

### 7.8 Late Result

Late Result 是在 Timeout、Cancellation、Lease 过期、Attempt 被替换、State Version 已推进、Deadline 已到或 Task 已进入终态后才返回的结果。它不能因为“内容看起来正确”就直接覆盖当前状态。

结果提交前至少应验证：

```text
attempt_id 仍是活动 Attempt
lease_epoch 仍是当前 Epoch
expected_state_version 与当前版本一致
取消策略允许提交
Deadline 策略允许提交
```

迟到结果有三类处理策略：

1. **Reject**：默认拒绝正式提交；
2. **Accept by explicit policy**：仅在无副作用、结果仍新鲜且状态未冲突时，由显式策略接纳；
3. **Reconcile**：把结果作为外部事实或证据进入对账、补偿或人工判断。

无效 Attempt 的结果不能成为当前正式结果，但应保留为 Observation / Audit Evidence，帮助诊断实际发生了什么。

### 7.9 竞争条件与条件提交

时间控制最危险的部分通常不是定时器本身，而是并发竞态：

```text
Result vs Cancel
Cancel vs Commit
Lease Expire vs Renew
Deadline vs Success
```

这些竞态不能依赖事件“碰巧先到”的进程内判断，而应使用条件更新或 Compare-and-Set：

```text
commit result
only if
  state_version == expected_version
  and active_attempt_id == attempt_id
  and lease_epoch == expected_epoch
  and state permits commit
```

因此，Timeout、Cancellation 和 Lease 最终都必须落到正式状态机与持久化提交规则上。

### 7.10 Human Waiting 的时间语义

Human-in-the-loop 需要分别设置：

- Human Response Timeout：人多久未响应后触发提醒、升级或默认分支；
- Task / Run Deadline：整个业务最晚何时结束；
- Active Compute Budget：实际计算可消耗多久；
- Wall-clock Duration：从创建到终态的自然时间。

暂停 Active Compute Budget 不等于取消端到端 Deadline。若业务允许人工等待暂停 Deadline，也必须由显式政策决定，并设置最长暂停时长、升级路径和最终终止条件，避免无限等待。

### 7.11 推荐状态字段

```yaml
timing:
  started_at: "..."
  deadline_at: "..."
  attempt_timeout_at: "..."
  idle_timeout_at: "..."
  last_progress_at: "..."

lease:
  holder: worker-3
  epoch: 9
  expires_at: "..."

cancellation:
  status: CANCEL_REQUESTED
  requested_at: "..."
  requested_by: user-7
  reason: USER_REQUEST
  scope: RUN

external_operation:
  operation_id: payment-42
  outcome: UNKNOWN
  reconciliation_status: REQUIRED
```

这些字段需要与 Run、Task、Attempt、State Version 和 Event 关联，才能支持恢复、审计和竞态判断。

### 7.12 当前阶段结论

1. Timeout 是相对时长，Deadline 是端到端绝对时间点；
2. Run Timeout 与 Idle Timeout 必须分开；
3. Heartbeat 只提供存活或进展证据；
4. Heartbeat 可以刷新 Idle Timeout，但不延长硬 Timeout 或 Deadline；
5. Deadline 必须向子任务传播，Retry 只能消耗剩余预算；
6. Timeout 不证明外部动作失败或副作用未发生；
7. Lease 是临时执行权，Heartbeat 可以支持续租，但不等于 Lease；
8. Fencing Token 用于阻止失效 Worker 提交；
9. Cancellation 是持久化的停止请求，不是瞬时事实；
10. Worker 应在安全边界协作式响应取消；
11. 级联取消通常停止子任务，但补偿、审计和对账可能继续；
12. 不可取消的外部动作必须查询、对账或补偿；
13. Late Result 默认不得覆盖当前正式状态；
14. 迟到结果应按 Reject、显式 Accept 或 Reconcile 处理；
15. `CANCELLED` 应表示计算已停止，且外部效果边界已经明确或被显式标记为待确认。

## 8. Checkpoint、Pause 与 Resume

Checkpoint、Pause 与 Resume 共同构成跨进程、跨时间和跨版本恢复的协议，而不是三个孤立功能：

```text
Checkpoint → 冻结可恢复事实
Pause      → 将运行转换为持久等待
Resume     → 重新验证后创建新的受控执行
```

Checkpoint 不是普通内存快照，而是某个一致性边界上，足以判断哪些事实已经提交、哪些效果可能发生以及接下来允许做什么的恢复契约：

```text
Checkpoint
= Committed State
+ Execution Position
+ Effect Ledger
+ Pending Waits
+ Version Information
+ Recovery Policy
```

判断 Checkpoint 是否充分，关键不在保存的数据量，而在于所有进程消失后，Runtime 能否仅凭持久化事实安全计算下一步，识别已完成工作和不确定副作用，拒绝失效结果，并解释恢复决策。这种性质可以称为恢复闭包（Recovery Closure）。

Pause 不是 `sleep`、无限 `await` 或长期保留 Worker。可靠暂停需要停止新动作，在安全边界提交状态，登记结构化等待条件，释放 Lease、连接和计算资源，并进入正式状态：

```text
RUNNING
→ PAUSE_REQUESTED
→ PAUSING
→ PAUSED / WAITING
```

Resume 也不是返回旧进程的下一行，而是：

```text
加载正式状态
→ 验证恢复事件、权限与版本
→ 对账未决副作用
→ 获取新的 Lease 和执行权
→ 重新计算 Ready Work
→ 重建最小充分 Context
→ 创建新的执行 Attempt
```

Run 和 Task 身份可以保持稳定，但 Worker、Lease、Fencing Epoch、Resume Epoch 和执行 Attempt 应重新建立。

Checkpoint 的实现通常有三类：

| 模型 | 核心机制 | 优点 | 主要约束 |
|---|---|---|---|
| 状态快照 | 保存正式 State 与下一执行位置 | 恢复直接、当前状态易查询 | 必须保证一致性切面，大状态成本高 |
| 事件历史与 Replay | 从初始输入和已提交事件重放编排逻辑 | 因果和审计完整 | 编排逻辑需要确定性，版本升级敏感 |
| 快照与事件混合 | 最近快照加后续事件，并独立保存 Effect Ledger | 兼顾读取、审计和恢复效率 | 存储与一致性协议更复杂 |

对 Agent 编排，更适合使用混合认知模型：

```text
Event Log           保存事实和因果关系
+ Periodic Snapshot 缩短恢复时间
+ Effect Ledger     管理外部副作用
+ Artifact Store    存放大型内容
```

### 8.1 检查点内容

Checkpoint 不是一个巨大的 State JSON，而是一组共同形成恢复闭包的事实：

```text
Checkpoint
├── 身份与谱系
├── Definition 与 Schema 版本
├── 正式状态引用
├── 执行游标
├── Task / Attempt 账本
├── 外部副作用账本
├── Pending Work
├── Wait Condition
├── Budget 与控制状态
├── Artifact 与 Context 重建引用
└── 完整性与安全元数据
```

#### 8.1.1 身份、谱系与建立原因

```yaml
checkpoint:
  checkpoint_id: cp-0018
  run_id: run-0007
  task_id: task-0012
  source_attempt_id: attempt-0003
  state_version: 34
  sequence: 18
  parent_checkpoint_id: cp-0017
  created_at: "..."
  created_reason: HUMAN_WAIT
```

`checkpoint_id` 提供稳定身份，`sequence` 和 `parent_checkpoint_id` 建立顺序、分支与回放谱系，`source_attempt_id` 说明由哪次执行生成，`state_version` 将恢复点绑定到正式状态。

`created_reason` 应为结构化枚举，而不是普通备注，例如：

```text
STEP_COMMITTED
SIDE_EFFECT_CONFIRMED
WAITING_FOR_HUMAN
WAITING_FOR_EVENT
RETRY_SCHEDULED
PAUSED_BY_USER
PAUSED_BY_POLICY
BUDGET_EXHAUSTED
MIGRATION_BOUNDARY
SERVICE_SHUTDOWN
```

#### 8.1.2 Definition、Schema 与 Worker 版本

```yaml
definition:
  orchestration_name: research-agent
  definition_version: "3.2.0"
  graph_hash: "sha256:..."
  state_schema_version: "2"
  event_schema_version: "4"
  prompt_versions:
    planner: planner-v7
    synthesizer: synthesizer-v4
  policy_version: policy-v12
  tool_contract_versions:
    web_search: "v3"
    publish_report: "v2"
```

需要分别管理：

```text
Definition Version
≠ State Schema Version
≠ Runtime / Worker Build Version
```

Definition 描述任务图与状态转换，State Schema 描述持久化结构，Worker Version 描述哪类执行者可以处理当前定义。Prompt、Policy 和 Tool Contract 也应单独版本化，不能全部压缩为一个含义模糊的 `version`。

#### 8.1.3 正式 State 与执行游标

小型 State 可以内嵌，但大型 State 更适合使用不可变版本引用：

```yaml
state:
  state_ref: state://run-7/version-34
  state_version: 34
  state_hash: "sha256:..."

cursor:
  current_node: request_approval
  node_phase: WAITING
  next_transition: validate_approval
  completed_nodes:
    - analyze_request
    - create_plan
  ready_nodes: []
```

执行游标应指向稳定的业务节点、状态转换或事件位置，不能依赖代码行、调用栈或局部变量。不同拓扑需要记录不同的恢复位置：

- Sequential：已完成阶段与下一阶段；
- Parallelize：各 Branch 状态、Fan-out 身份和 Join 状态；
- Orchestrator–Workers：规划轮次、已分配任务和等待集合；
- Agent Loop：迭代次数、最后提交动作和终止检查结果。

#### 8.1.4 Task、Attempt 与执行权账本

Checkpoint 必须能区分已完成、未开始、等待重试、正在运行和已经失效的 Task / Attempt：

```yaml
tasks:
  - task_id: task-12
    status: COMPLETED
    accepted_attempt_id: attempt-3
    result_ref: artifact-17

  - task_id: task-13
    status: WAITING_RETRY
    active_attempt_id: null
    last_attempt_id: attempt-5
    retry_budget_remaining: 2
    next_retry_at: "..."

  - task_id: task-14
    status: RUNNING
    active_attempt_id: attempt-8
    lease_epoch: 4
```

对于 `RUNNING` Task，还要记录执行权和提交状态：

```yaml
execution_claim:
  attempt_id: attempt-8
  worker_id: worker-3
  lease_epoch: 4
  lease_expires_at: "..."
  last_heartbeat_at: "..."
  result_commit_status: NOT_COMMITTED
```

恢复时据此判断应等待、查询、使旧 Attempt 失效、创建新 Attempt，还是接受已经正式提交的结果。

#### 8.1.5 Effect Ledger

只保存 State 而不保存副作用账本，是 Agent 恢复中最危险的设计之一：

```yaml
effects:
  - operation_id: email-send-42
    task_id: task-15
    attempt_id: attempt-7
    effect_type: EMAIL_SEND
    idempotency_key: run-7-notification-1
    request_hash: "sha256:..."
    phase: REQUEST_SUBMITTED
    outcome: UNKNOWN
    provider_operation_id: provider-msg-92
    receipt_ref: null
    reconciliation_required: true
```

Effect Phase 至少应表达：

```text
PROPOSED
VALIDATED
COMMIT_STARTED
REQUEST_SUBMITTED
CONFIRMED_SUCCEEDED
CONFIRMED_FAILED
OUTCOME_UNKNOWN
COMPENSATION_REQUIRED
COMPENSATED
```

如果外部系统已经执行，但 Runtime 在保存结果前崩溃，`REQUEST_SUBMITTED + OUTCOME_UNKNOWN` 会要求恢复流程先查询和对账，而不是再次执行。没有结果记录不等于副作用没有发生。

#### 8.1.6 Pending Work

Checkpoint 还需要表达尚未完成但已经形成调度意图的工作：

```yaml
pending_work:
  - work_id: work-28
    task_id: task-16
    action: EXECUTE_NODE
    node: generate_report
    status: READY
    earliest_start_at: "..."
    deadline_at: "..."
    required_capabilities:
      - report_generation
```

可区分 `PLANNED`、`READY`、`DISPATCHED`、`CLAIMED`、`COMPLETED` 和 `SUPERSEDED`。不过，Ready Work 应尽量能从正式 State 和依赖关系重新计算；Checkpoint 中的 Ready Set 是加速索引，不应成为唯一事实来源。

#### 8.1.7 Wait Condition

暂停必须登记一个结构化、可验证和可单次消费的恢复协议：

```yaml
wait_condition:
  wait_id: wait-21
  wait_type: HUMAN_APPROVAL
  correlation_id: approval-request-9
  expected_event_types:
    - APPROVED
    - REJECTED
    - CHANGE_REQUESTED
  subject:
    operation_id: payment-42
    state_version: 34
    request_hash: "sha256:..."
  authorization:
    required_role: finance_approver
    allowed_actor_ids:
      - user-17
  timing:
    created_at: "..."
    expires_at: "..."
    reminder_at: "..."
  consumption:
    consumed: false
    consumed_event_id: null
```

人工响应应满足：

```text
Actor-bound
+ Scope-bound
+ Version-bound
+ Time-bound
+ Single-use
```

批准 State Version 34 的响应，不能自动应用到已经变化的 Version 37。

#### 8.1.8 Budget、Deadline 与控制状态

```yaml
control:
  run_deadline_at: "..."
  task_deadline_at: "..."
  active_compute_used_ms: 47000
  active_compute_remaining_ms: 13000
  token_budget_remaining: 8200
  monetary_budget_remaining: 1.72
  retry_budget_remaining: 2
  cancellation_status: NONE
  pause_status: WAITING_FOR_HUMAN
```

暂停不等于所有时间和预算语义消失，Resume 也不应重新获得完整 Token、时间、货币、Retry、Fan-out 或 Handoff 预算：

```text
Resume ≠ Budget Reset
```

#### 8.1.9 Artifact 与 Context 重建信息

大型模型输出、文件和工具结果应保存为不可变 Artifact 引用。Context 不应整体冻结为旧 Prompt，而应保存重建规则和正式来源：

```yaml
context_reconstruction:
  projection_spec: context-projection-v5
  source_state_version: 34
  required_artifacts:
    - artifact-plan-8
    - artifact-evidence-12
  required_messages:
    from_sequence: 42
    to_sequence: 56
  pinned_facts:
    - fact-17
    - decision-9
```

恢复时应使用正式 State、当前有效 Artifact、仍然成立的决策、新 Resume Event 和当前 Policy 重新投影最小充分 Context。旧 Context 是历史证据，不应默认继续有效。

#### 8.1.10 完整性、安全与保留

```yaml
integrity:
  content_hash: "sha256:..."
  previous_hash: "sha256:..."
  encryption_key_version: key-v4
  classification: CONFIDENTIAL
  retention_policy: run-plus-90-days
```

Checkpoint 应支持篡改检测、敏感字段加密、租户隔离、访问控制和保留策略。Secret 只保存受控引用，不能把明文 Token、Webhook Secret 或 OAuth Credential 写入普通 Checkpoint。

#### 8.1.11 三种完整度

| 类型 | 内容 | 适用场景 |
|---|---|---|
| Lightweight Checkpoint | State Version、Execution Cursor、Definition Version | 可重复计算、无副作用的小步骤 |
| Durable Checkpoint | Lightweight 加 Task / Attempt、Pending Work、Wait、Budget | 跨进程和长运行恢复 |
| Effect-safe Checkpoint | Durable 加 Effect Ledger、幂等身份、Receipt、对账与补偿状态 | 支付、发送、发布、部署等副作用 |

不同类型可以共享统一 Envelope，但应根据执行风险扩展恢复内容，而不是为所有步骤复制同样大的状态。

#### 8.1.12 一致提交边界

State 更新、Task 转移、Checkpoint、Effect Record 和 Outbox Event 如果分别提交，崩溃可能产生相互矛盾的事实。对同一本地存储中的正式记录，应尽量原子提交：

```text
BEGIN
  compare state_version
  write new state
  transition task
  write checkpoint
  write effect record
  write outbox event
COMMIT
```

外部系统无法参与本地数据库事务，因此 Checkpoint 不能制造真正的跨系统原子性。外部副作用仍需要 Operation ID、Idempotency Key、Outbox / Inbox、结果查询和对账机制。

#### 8.1.13 当前阶段结论

1. Checkpoint 是恢复契约，不是内存快照；
2. Checkpoint 必须绑定 Run、Task、Attempt 和 State Version；
3. Checkpoint 应具有不可变身份、顺序和父子谱系；
4. Definition、State Schema、Worker、Prompt、Policy 和 Tool Contract 应分别版本化；
5. 执行游标应指向稳定业务节点，而不是代码行；
6. Task Ledger 必须区分 Task 与 Attempt；
7. `RUNNING` 状态还必须记录 Lease、Epoch 和提交状态；
8. 副作用必须进入 Effect Ledger，未知结果不能被当作未发生；
9. Pending Work 应尽量可从正式 State 重新计算；
10. Pause 必须保存结构化 Wait Condition；
11. Human Response 必须绑定身份、范围、版本、期限并单次消费；
12. Resume 不能重置预算和 Deadline；
13. Context 应在恢复时重新投影，而不是机械复用旧 Prompt；
14. State、Checkpoint、Task Transition、Effect Record 和 Outbox 应形成一致提交边界；
15. 外部副作用无法与本地 Checkpoint 完全原子化，必须依赖幂等与对账。

### 8.2 检查点时机

检查点时机的本质不是每隔多久保存一次，而是选择哪些事实一旦被接受，就必须在继续执行前形成可恢复的持久化边界：

```text
Checkpoint Timing
≠ 纯时间间隔
= 语义边界
+ 风险边界
+ 成本策略
```

Checkpoint 太少会增加重算、重复副作用和丢失并行结果的风险；太多则会增加序列化、事务、存储和并发协调成本。合理策略是在风险发生前建立恢复边界，在事实被接受后及时提交，并在低风险计算中按成本控制频率。

#### 8.2.1 三类触发方式

| 类型 | 典型触发 |
|---|---|
| 语义触发 | Task 完成、Gate 通过、计划被接受、Handoff、Join、进入终态 |
| 风险触发 | 即将执行写操作、结果未知、重试、取消、Lease 转移、版本升级 |
| 周期触发 | 每 N 步、每 T 秒、状态变化超过阈值、成本或进度达到阈值 |

```text
Checkpoint Policy
= Mandatory Semantic Boundaries
+ Mandatory Risk Boundaries
+ Adaptive Periodic Boundaries
```

周期策略只能限制低风险计算的最大重算量，不能替代副作用、等待和控制权转移等强制边界。

#### 8.2.2 步骤前、步骤后与步骤内

步骤前 Checkpoint：

```text
Checkpoint → Execute Step
```

适合确定性计算、只读查询和可安全重试的动作。崩溃后可以重跑整个 Step，但如果 Step 已成功而结果尚未提交，仍可能重复执行。

步骤后 Checkpoint：

```text
Execute Step → Validate Result → Checkpoint
```

适合昂贵计算、模型响应和并行分支结果，能够避免已接受结果丢失；但它不能单独解决“外部副作用成功、本地尚未保存”的窗口。

步骤内 Checkpoint 适合可以稳定分片的长计算：

```text
完成分片 100 → Checkpoint
完成分片 200 → Checkpoint
完成分片 300 → Checkpoint
```

前提是分片有稳定身份、进度可验证、结果可独立提交且重复处理安全。若模型 API 不能从指定 Token 继续，部分 Token 就不是真正的恢复点。

#### 8.2.3 正式 State 转换

```text
Proposal
→ Validate
→ Commit State Version
→ Checkpoint
→ Schedule Next Work
```

Task 完成、Gate 通过、计划被接受、Router 路径确认或 Handoff 成立后，应及时建立恢复边界。LLM 产生输出不等于正式状态成立；必须先验证和接受。

#### 8.2.4 进入等待之前

```text
Persist State
→ Create Wait Condition
→ Checkpoint
→ Publish Request
→ PAUSED / WAITING
```

等待人工、Timer、Webhook、子任务或外部作业前，必须先拥有可恢复的等待记录。发布请求与登记 Wait Condition 应通过本地事务加 Outbox 等机制协调，避免请求已经发出但 Runtime 不知道自己在等待什么。

#### 8.2.5 外部副作用双边界

副作用需要 Intent 与 Result 形成夹心式提交：

```text
Prepare / Intent Checkpoint
          ↓
    External Action
          ↓
Result / Reconciliation Checkpoint
```

调用前持久化 `operation_id`、Idempotency Key、Request Hash 和 `COMMIT_STARTED`；调用后保存 `CONFIRMED_SUCCEEDED`、`CONFIRMED_FAILED` 或 `OUTCOME_UNKNOWN`。调用超时时必须明确进入查询和对账流程，不能把未知结果当作未发生。

#### 8.2.6 Retry、Backoff 与控制权转移

```text
Attempt Failed
→ Classify Failure
→ Consume Retry Budget
→ Create Durable Timer
→ Checkpoint
→ WAITING_RETRY
```

Retry Timer、失败分类和剩余预算必须共同持久化。Handoff、Delegation 和 Lease 转移也应先提交原 Owner、新 Owner、Scope、State Version、权限和 Epoch，再允许目标 Worker 执行，以防两个 Owner 同时继续。

#### 8.2.7 Fan-out 与 Fan-in

Fan-out 前先提交稳定的 Branch Identity，再派发分支；每个分支完成后独立验证和提交；Join 读取已提交结果、校验 Join 条件后建立聚合 Checkpoint：

```text
Branch A → commit result-A
Branch B → commit result-B
Branch C → commit result-C
                 ↓
         Join → aggregate → checkpoint
```

不应等全部分支结束后才保存一次，也不应默认要求所有分支 Stop-the-world 建立全局物理快照。多数场景可以通过不可变分支结果、State Version 和条件提交形成逻辑一致性。

#### 8.2.8 Pause、Cancellation、Lease 与终态

Pause Request 被接受、Cancellation 已传播、Lease Epoch 推进、旧 Attempt 失效和新 Owner 获得执行权，都必须成为持久事实。进入 `COMPLETED`、`FAILED`、`CANCELLED`、`PARTIALLY_COMPLETED` 或 `RECONCILIATION_REQUIRED` 时，应提交最终 State、结果、Artifact、未决副作用、终止原因、未完成任务和后续责任。

#### 8.2.9 不同动作的策略

| 动作类型 | 推荐时机 |
|---|---|
| 纯确定性计算 | 周期性或阶段完成后 |
| 普通 LLM 调用 | 请求前记录 Attempt，结果验证接受后保存 |
| 昂贵 LLM 调用 | 持久化调用身份，完整响应尽快写入 Artifact |
| 流式 LLM 输出 | 不按每个 Token 保存，按可验证语义单元或完整响应提交 |
| 只读工具调用 | 根据成本、新鲜度和可重复性决定 |
| 外部写操作 | 调用前保存 Intent，调用后保存 Result / Unknown |
| 人工审批 | 发送请求前保存 Wait Condition |
| 长时间批处理 | 按稳定分片或进度游标保存 |
| 并行分支 | 各分支独立提交，Join 单独提交 |
| Handoff | 转移记录提交后再允许目标方执行 |

#### 8.2.10 LLM 与流式输出

```text
创建 Attempt
→ 保存 Request Identity
→ 调用模型
→ 原始响应写入 Artifact
→ 验证与结构化
→ 接受为正式结果
→ Commit State + Checkpoint
```

需要区分：

```text
Raw Model Response    → Observation / Artifact
Validated Proposal    → 候选状态
Accepted State Update → 正式 Checkpoint
```

Token Stream 通常只用于实时交付，可写入 Durable Output Buffer；完整响应或可验证章节才适合作为语义恢复点。中断后无法精确继续的部分应创建新 Attempt，而不是假装从某个 Token 恢复。

#### 8.2.11 周期与自适应策略

周期 Checkpoint 可以按时间、步骤、已处理项目数和状态变化量触发；风险感知策略还应考虑重算成本、副作用、并发冲突和剩余 Deadline：

```text
Checkpoint Total Cost
= Persistence Cost
+ Coordination Cost
+ Storage Cost
+ Expected Recovery Rework
+ Effect Uncertainty Risk
```

```text
Expected Recovery Rework
≈ Failure Probability
× Since-last-checkpoint Work
× Recompute Cost
```

低风险廉价计算可以降低频率；昂贵、非确定性或有副作用的动作应提高频率或使用强制边界。

#### 8.2.12 Shutdown Checkpoint 与反模式

优雅关闭时建立 Checkpoint 可以减少重算，但不能成为可靠性基础，因为 Runtime 还会遭遇崩溃、断电、强制终止、OOM 和网络分区。系统必须允许在任意两个正式提交边界之间失败。

典型反模式包括：

1. 只在整个 Run 结束时保存；
2. 每个 Token 都保存；
3. 外部动作执行后才第一次登记；
4. 将未验证模型输出保存为正式结果；
5. 只在应用优雅关闭时保存；
6. 所有并行分支共用全局 Checkpoint Barrier；
7. Checkpoint 与 State 分别提交；
8. 不判断 Checkpoint 语义就默认从节点后一步继续。

#### 8.2.13 当前阶段结论

1. Checkpoint 时机首先由语义和风险边界决定；
2. 正式 State Transition 被接受后应形成恢复边界；
3. 进入持久等待前必须提交 Wait Condition；
4. 外部副作用需要 Intent 与 Result 双边界；
5. `OUTCOME_UNKNOWN` 必须作为正式状态提交；
6. Retry Timer、失败分类和 Retry Budget 应共同持久化；
7. Handoff 和 Lease 转移必须先提交所有权变化；
8. Fan-out 前保存 Branch Identity，各分支独立提交，Join 再建立聚合边界；
9. 昂贵或非确定性结果应尽快保存为 Artifact；
10. 未验证模型输出不能直接进入正式 Checkpoint；
11. 流式 Token 通常不是独立恢复点；
12. 周期策略用于控制重算成本，不能替代强制语义边界；
13. Shutdown Checkpoint 是优化，不是可靠性基础；
14. Checkpoint 必须与 State、Task Transition、Effect Record 和 Outbox 保持一致；
15. 系统应允许在任意两个正式提交边界之间崩溃并安全恢复。

### 8.3 Durable State 与可重算状态

### 8.4 暂停原因

### 8.5 人工等待

### 8.6 服务重启恢复

### 8.7 代码或 Prompt 版本变化

### 8.8 恢复后的重复动作防护

### 8.9 长期无人响应

## 9. 并发、背压与资源治理

### 9.1 全局并发

### 9.2 用户与租户配额

### 9.3 Agent 和工具并发

### 9.4 Fan-out 上限

### 9.5 背压

### 9.6 优先级与公平性

### 9.7 Rate Limit

### 9.8 Token、时间与货币预算

### 9.9 预算继承与子任务分配

## 10. Termination 与收敛

开放式 Agent Loop 可能持续思考、调用工具、修改计划、Handoff 和自我修订。如果没有独立终止控制，系统可能无限调用工具、在 Agent 间往返、反复生成相似答案，在目标不可达或副作用未知时仍继续消耗预算。

Termination 不是 Prompt 中一句“完成后停止”，而是 Runtime 对目标、证据、进展、风险、预算和外部效果进行联合判断后执行的正式状态转换。

### 10.0 核心模型

#### 10.0.1 Termination、Completion 与 Convergence

三个概念回答不同问题：

| 概念 | 回答的问题 | 核心性质 |
|---|---|---|
| Termination | 为什么不再继续执行？ | 控制决策 |
| Completion | 目标和成功标准是否已经满足？ | 业务语义 |
| Convergence | 继续一轮是否还会带来有意义变化？ | 轨迹性质 |

```text
Terminated ≠ Succeeded
Converged  ≠ Correct
Converged  ≠ Complete
```

Run 可以因为用户取消、Deadline、Budget、安全策略、无进展、循环、依赖不可用或人工升级而终止；这些原因都不能被自动解释为完成。Agent 也可能稳定地停留在错误答案或不可行动状态上。

#### 10.0.2 三维终态

单一的 `COMPLETED` 或 `FAILED` 无法表达停止原因、目标满足情况和外部效果。建议至少拆成三维：

```yaml
termination:
  execution_status: TERMINATED
  outcome: PARTIALLY_SUCCEEDED
  reason: DEADLINE_EXCEEDED
  effect_status: RECONCILIATION_REQUIRED
```

Outcome 可以包括：

```text
SUCCEEDED
PARTIALLY_SUCCEEDED
UNSATISFIED
UNKNOWN
```

Termination Reason 可以包括：

```text
GOAL_SATISFIED
USER_CANCELLED
DEADLINE_EXCEEDED
BUDGET_EXHAUSTED
MAX_STEPS_REACHED
MAX_DEPTH_REACHED
MAX_HANDOFFS_REACHED
LOOP_DETECTED
NO_PROGRESS
MARGINAL_GAIN_TOO_LOW
POLICY_BLOCKED
DEPENDENCY_UNAVAILABLE
UNRECOVERABLE_FAILURE
HUMAN_ESCALATION
```

Effect Status 可以包括：

```text
CLEAN
CONFIRMED
PENDING
OUTCOME_UNKNOWN
COMPENSATION_REQUIRED
RECONCILIATION_REQUIRED
```

#### 10.0.3 分层 Termination Guard

终止判断不应只交给单个 LLM Judge，而应分层：

```text
1. Safety / Policy / Cancellation
2. Hard Limits
3. Semantic Completion
4. Feasibility / Blocking
5. Progress / Convergence
6. Continue / Replan / Degrade / Escalate / Terminate
```

安全、权限和 Cancellation 优先检查；Deadline、Token、货币、最大步骤、深度、Fan-out、Handoff 和 Retry 等硬限制由 Runtime 确定性执行；之后再判断语义完成、可行性和收敛情况。

发现异常后也不一定立即终止。Control Plane 可以选择：

```text
CONTINUE
REPLAN
RETRY
DEGRADE
PAUSE
ESCALATE
TERMINATE
```

#### 10.0.4 终止状态机

Worker 或 LLM 不应直接把 Run 写成终态：

```text
RUNNING
→ TERMINATION_PROPOSED
→ TERMINATION_VERIFYING
→ TERMINATION_COMMITTING
→ COMPLETED / PARTIAL / FAILED
  / CANCELLED / EXHAUSTED / ESCALATED
  / RECONCILIATION_REQUIRED
```

`TERMINATION_PROPOSED` 可以由 Agent、Worker、Planner、Evaluator、Runtime、Policy Engine、用户或人工审批者提出。验证阶段检查 Goal、Required Task、Artifact、Evidence、Effect、Late Result 和 State Version；提交阶段停止派发新任务，取消或 Drain 子任务，拒绝失效结果，对账外部效果，保存最终 Artifact 和 Final Checkpoint，并发布终态 Event。

如果验证不通过，应返回 `RUNNING`、`REPLAN`、`PAUSED` 或 `ESCALATED`，不能为了结束而降低完成标准。

#### 10.0.5 有意义的进展

生成更多 Token、调用更多工具或执行更多 Step 只是活动量，不等于进展。进展可以抽象为：

```text
Progress(t)
= ΔGoalCoverage
+ ΔEvidenceCoverage
+ ΔStateCommitment
+ ΔUncertaintyReduction
+ ΔConstraintSatisfaction
- ΔRisk
- Cost
```

一次工具调用即使返回大量数据，如果没有提高 Goal Coverage、增加独立证据、产生正式状态变化、降低关键不确定性或满足新约束，也可能不构成有效进展。

#### 10.0.6 收敛形态

| 收敛类型 | 典型表现 |
|---|---|
| State Convergence | 多轮没有正式 State 变化 |
| Action Convergence | 反复提出相同或近似动作 |
| Plan Convergence | 计划反复改写但任务图不变 |
| Evidence Convergence | 新检索不再增加独立证据 |
| Answer Convergence | 候选答案多轮变化很小 |
| Utility Convergence | 预期边际收益低于额外成本与风险 |

这些信号可以支持停止或重规划，但不能单独证明目标完成。

#### 10.0.7 循环类型与识别

循环不只是完全相同字符串的重复，常见类型包括：

- Exact Action Loop；
- 语义相同但参数措辞变化的 Semantic Loop；
- Agent 间 Handoff Ping-pong；
- 没有策略变化的 Retry Loop；
- 生成、否定再生成近似计划的 Planning Loop；
- 没有增加正确性或证据的 Self-critique Loop；
- 工具返回相同结果但 Agent 继续调用的 Tool–Reasoning Loop。

检测信号可以组合：

```text
Action Fingerprint
State Hash
Plan Graph Hash
Evidence Set Hash
Handoff Path
Failure Signature
Semantic Similarity
```

语义相似度只能生成候选告警，最终仍要结合正式 State、Goal 和等待状态判断，避免把合法轮询或人工等待误判为循环。

#### 10.0.8 Completion Contract

完成条件应尽量在 Run 开始前形成结构化契约：

```yaml
completion_contract:
  goal: "生成并发布经过审批的研究报告"
  required_deliverables:
    - report_document
  required_conditions:
    - factual_review_passed
    - security_review_passed
    - human_approval_received
  required_effects:
    - publication_confirmed
  evidence_requirements:
    minimum_independent_sources: 3
  optional_improvements:
    - add_visual_summary
    - add_appendix
```

```text
Required 条件未满足 → 不能宣称成功
Optional 条件未满足 → 可以完成
Hard Limit 到达     → 可以停止，但不等于成功
```

#### 10.0.9 Task 与 Run 的完成聚合

单个 Task 完成不等于 Run 完成。父级需要区分 `required_children`、`optional_children`、`compensation_children`、`reconciliation_children` 和 `detached_children`：

```text
Run Completed
only if
  all required children satisfied
  and no blocking unknown effects
  and completion contract satisfied
```

Optional Child 可以取消或跳过；Required Child 的结果仍为 `OUTCOME_UNKNOWN` 时，Run 不能进入干净的 `COMPLETED`。

#### 10.0.10 Hard、Soft、Graceful 与 Degradation

| 方式 | 语义 |
|---|---|
| Hard Stop | 立即阻止继续运行，适用于严重安全或基础设施风险 |
| Soft Stop | 不再创建新动作，允许当前工作到达安全提交边界 |
| Graceful Termination | Drain / Cancel 子任务、保存有效结果、对账副作用并提交终态 |
| Graceful Degradation | 在显式政策允许时降低目标或执行策略 |

降级必须对用户透明，不突破安全和权限约束，也不能把降级结果伪装成原目标完全成功。

#### 10.0.11 结构化终止决策

Termination Guard 不应只返回 `should_stop: true`：

```yaml
termination_decision:
  decision: TERMINATE
  proposed_outcome: PARTIALLY_SUCCEEDED
  reason: BUDGET_EXHAUSTED
  goal_satisfaction:
    satisfied: false
    completed_criteria:
      - report_generated
    missing_criteria:
      - human_approval
      - publication_confirmed
  progress:
    status: LOW
    no_progress_iterations: 3
  effects:
    status: CLEAN
  next_action:
    type: RETURN_PARTIAL_RESULT
```

如果仍有可行路径，决策可以是 `REPLAN`、`PAUSE` 或 `ESCALATE`，并明确失效计划和后续约束。

#### 10.0.12 模型与 Runtime 的职责边界

LLM 适合提出目标是否可能满足、还缺少什么、继续执行的预期价值、是否出现语义重复以及是否应该重规划。Runtime 和确定性组件负责强制 Deadline、Budget、最大步骤、深度、Handoff、Cancellation、权限、State Version、Required Task 状态和 Effect 确认。

```text
LLM proposes termination
Runtime verifies invariants
Control Plane commits terminal state
```

#### 10.0.13 当前阶段结论

1. Termination 是停止执行，Completion 是目标满足，Convergence 是边际变化趋近于零；
2. 停止不等于成功，收敛不等于正确；
3. Outcome、Termination Reason 和 Effect Status 应分开建模；
4. 安全、取消和硬限制优先于普通语义判断；
5. 完成条件应在运行开始前形成 Completion Contract；
6. LLM 可以提出完成，但不能直接提交终态；
7. Runtime 必须确定性强制 Deadline、Budget 和执行上限；
8. 进展应基于 Goal、Evidence、State、Uncertainty 和 Constraint；
9. 循环检测需要结合 Action、State、Plan、Evidence、Handoff 和 Failure Signature；
10. Required Child 未完成或副作用未知时，Run 不能宣称干净完成；
11. 终止前应停止派发、处理子任务、对账副作用并提交 Final Checkpoint；
12. Graceful Degradation 必须显式、透明且不能突破安全约束；
13. Resume、Retry 或 Replan 不应重置终止预算；
14. 终止决策应输出结构化理由、缺失条件和后续责任；
15. 编排系统不仅要保证最终停止，还要保证停止在可解释、有界且业务语义正确的终态。

### 10.1 任务完成条件

完成条件是版本化、可验证的契约，而不是 Agent 的主观完成感：

```text
Agent proposes completion
→ Completion Contract evaluates
→ Evidence validates
→ Runtime checks invariants
→ Control Plane commits terminal state
```

模型可能因为已经生成文本、没有想到下一步、工具失败、Context 或 Budget 接近耗尽而提出完成，这些都不能直接成为正式终态。

#### 10.1.1 Completion Contract 的条件类型

一个完整 Completion Contract 通常包含：

```text
Completion Contract
├── Goal Conditions
├── Deliverable Conditions
├── State Conditions
├── Constraint Conditions
├── Evidence Conditions
├── Effect Conditions
└── Approval Conditions
```

Goal Condition 描述业务目标是否满足；Deliverable Condition 验证必要 Artifact 是否存在、属于当前 Run、具有正确版本和 `FINAL` 状态；State Condition 基于正式 State 判断业务事实；Constraint Condition 负责预算、地区、安全等约束；Evidence Condition 声明所需来源、新鲜度和独立性；Effect Condition 明确外部动作需要达到的确认级别；Approval Condition 绑定审批者、Scope 和 State Version。

```yaml
completion_contract:
  goal: "生成并发布经过审批的研究报告"
  goal_conditions:
    - id: goal.report_usable
      required: true
  required_deliverables:
    - artifact_type: research_report
      required_status: FINAL
  state_conditions:
    - id: report.reviewed
      predicate: factual_review_status == PASSED
  hard_constraints:
    - id: security.review
      predicate: security_review_status == PASSED
  evidence_requirements:
    minimum_independent_sources: 3
  required_effects:
    - operation: publication
      assurance: CONFIRMED_SUCCEEDED
  required_approvals:
    - type: COMPLIANCE_REVIEW
      must_bind_current_state_version: true
  optional_improvements:
    - add_visual_summary
```

10.1 定义需要什么证据，证据是否充分、可信和独立留到 §10.2 深入。

#### 10.1.2 Required、Optional 与 Veto

至少应区分：

- `Required`：缺失就不能成功；
- `Optional`：影响质量，但不阻止成功；
- `Veto / Hard Constraint`：一旦违反就否决成功。

```text
Complete
only if
  every Required Criterion == SATISFIED
  and every Veto Criterion != VIOLATED
  and no Blocking Unknown exists
```

完成多个 Optional 不能抵消 Required 缺失，加权高分也不能覆盖安全违规、越权或必要审批缺失。

#### 10.1.3 Criterion 状态逻辑

普通布尔值不足以表达长运行和外部系统的不确定性。Criterion 应至少支持：

```text
SATISFIED
UNSATISFIED
UNKNOWN
NOT_APPLICABLE
```

`UNKNOWN` 表示支付结果、迟到任务、Artifact 写入或审批回调尚不能确认；Required Criterion 为 `UNKNOWN` 时不能进入干净的 `SUCCEEDED`，而应等待、对账、升级或按显式政策进入部分终态。

`NOT_APPLICABLE` 必须由 Completion Contract 的条件分支和正式 State 推导，不能由 Agent 为了完成自行标记。

#### 10.1.4 稳定 Criterion Identity 与验证记录

自然语言描述容易漂移，每个条件应有稳定 ID：

```yaml
criterion_result:
  criterion_id: report.sections.complete
  status: SATISFIED
  evaluated_state_version: 34
  evaluated_at: "..."
  validator:
    type: DETERMINISTIC
    version: report-schema-v2
  evidence_refs:
    - artifact-report-17
    - validation-result-9
```

若模型参与语义判断，还应记录 Model、Prompt Version 和 Decision Artifact，但 Model-assisted 判断仍需要 Control Plane 接受。

验证必须绑定 State Version。若报告在 Version 34 通过验证，Version 35 又被修改，旧验证不能自动证明新版本仍满足条件：

```text
State Changed
→ Identify Invalidated Criteria
→ Re-evaluate Affected Criteria
```

#### 10.1.5 Contract 演化

长运行中可以调整 Completion Contract，但必须显式授权、版本化并可审计：

```yaml
completion_contract:
  contract_id: completion-contract-7
  version: 3
  supersedes_version: 2
  changed_by: user-17
  change_reason: "用户取消发布要求，只需要最终报告"
```

变更记录应说明谁修改、为什么修改、影响哪些验证和审批，以及是否需要重新预算。Agent 不能因为条件难以满足就静默删除条件或降低标准。

#### 10.1.6 条件化条件

并非所有条件对所有执行分支都适用：

```yaml
branches:
  draft_only:
    required:
      - report_generated
  publish:
    required:
      - report_generated
      - human_approval
      - publication_confirmed
```

或者：

```yaml
conditional_criterion:
  criterion_id: payment.confirmed
  applies_if: selected_plan.requires_payment == true
  required_when_applicable: true
```

是否适用应由正式 State 和确定性规则决定，避免为未触发分支等待，也避免 Agent 自行解释条件。

#### 10.1.7 正向与负向条件

完成既可能要求报告、审批或发布存在，也可能要求不存在阻塞项，例如没有开放的 Critical Finding、没有 Required Task 仍在运行、没有未知的必要副作用。

负向条件必须定义封闭检查范围：

```yaml
criterion:
  id: no-open-critical-findings
  scope: security_review_findings
  predicate: count(status=OPEN, severity=CRITICAL) == 0
```

“在已定义集合中没有阻塞项”可以验证；“世界上没有任何遗漏或错误”属于开放世界断言，通常无法证明。

#### 10.1.8 父子任务完成聚合

Child Task 对父级完成的作用可以是：

| 类型 | 聚合语义 |
|---|---|
| ALL Required | 所有必要子任务都满足 |
| ANY-of | 多条替代路径至少一条满足 |
| Quorum | 规定数量的参与者满足 |
| Threshold | 达到覆盖率或业务阈值 |
| Optional | 不阻塞父任务，但应说明未完成 |
| Compensation | 修复失败后的副作用 |
| Reconciliation | 确认未知外部事实 |
| Detached | 不阻塞父级生命周期，必须显式声明 |

```yaml
child_completion:
  all_required:
    - task-a
    - task-b
  any_of:
    - task-c-primary
    - task-c-fallback
  quorum:
    tasks:
      - reviewer-1
      - reviewer-2
      - reviewer-3
    minimum_satisfied: 2
  optional:
    - generate-appendix
```

#### 10.1.9 并行任务与 Join

Parallelize 或 Orchestrator–Workers 不能只判断 `completed_count == total_count`。Join 必须理解 Required、Optional、Cancelled、Superseded、Late Result、`OUTCOME_UNKNOWN`、Compensation 和 Reconciliation：

```yaml
branch_result:
  branch_id: source-research-b
  task_id: task-12
  accepted_attempt_id: attempt-3
  status: SATISFIED
  result_ref: artifact-17
  state_version: 34
```

Join 只能读取当前有效 Branch 和 Accepted Attempt 的正式结果，校验 State Version，并拒绝 Late Result 覆盖已经完成的聚合。

#### 10.1.10 Partial Completion

部分完成不能在 Deadline 到达后临时创造，必须由 Contract 预先允许并定义 Minimum Viable Outcome：

```yaml
partial_completion:
  allowed: true
  minimum_required:
    - report_draft_generated
    - limitations_disclosed
  omitted_items_must_be_reported: true
  forbidden_if:
    - critical_safety_issue_open
    - payment_outcome_unknown
```

如果 Contract 未允许 Partial，只完成部分条件时 Outcome 应为 `UNSATISFIED`，而不是擅自标记 `PARTIALLY_SUCCEEDED`。

#### 10.1.11 外部效果的确认级别

```text
Request Recorded
→ Provider Accepted
→ Effect Executed
→ Business Outcome Confirmed
```

邮件的已入队、已发送、已投递、已读取，支付的请求已提交、渠道已接受、资金已扣除和业务账务已确认，都不是同一层级。Effect Condition 必须声明所需 Assurance：

```yaml
effect_condition:
  operation: email-42
  required_assurance: PROVIDER_ACCEPTED
```

否则不同组件会把“已请求”和“已实现业务效果”都称为完成。

#### 10.1.12 完成验证与并发提交

推荐顺序：

```text
1. Freeze Candidate State Version
2. Resolve Applicable Criteria
3. Evaluate Deterministic Criteria
4. Validate Artifacts and Effects
5. Evaluate Semantic Criteria
6. Aggregate Child Results
7. Check Blocking Unknowns
8. Produce Completion Decision
9. CAS Commit Terminal State
```

验证期间可能有 Worker 返回结果，因此终态提交必须绑定同一个候选版本：

```text
commit COMPLETED
only if
  current_state_version == evaluated_state_version
  and termination_state == TERMINATION_VERIFYING
  and no new blocking event exists
```

版本变化时应重新评估受影响条件，不能把 Version 34 的验证结果用于提交 Version 35。

#### 10.1.13 结构化 Completion Decision

```yaml
completion_decision:
  contract_id: completion-contract-7
  contract_version: 3
  evaluated_state_version: 34
  outcome: NOT_COMPLETE
  criteria:
    satisfied:
      - report.generated
      - evidence.minimum_sources
    unsatisfied:
      - approval.compliance
    unknown:
      - effect.publication
    not_applicable:
      - payment.confirmed
  blocking_reasons:
    - REQUIRED_APPROVAL_MISSING
    - REQUIRED_EFFECT_UNKNOWN
  suggested_control_action:
    type: WAIT
    wait_for:
      - compliance-approval
      - publication-reconciliation
```

Completion Decision 应明确满足、缺失、未知和不适用条件，提出 `WAIT`、`REPLAN`、`ESCALATE` 或终止建议，而不是只返回布尔值。

#### 10.1.14 典型反模式

1. Agent 输出 `done` 就结束；
2. 只检查是否存在最终文本；
3. 用总分覆盖硬约束；
4. 将 `UNKNOWN` 当作成功；
5. Agent 自行删除难以满足的条件；
6. Optional Task 阻止整个 Run；
7. Required Task 失败却被完成数量掩盖；
8. 旧审批继续作用于新 Artifact；
9. 使用“没有任何错误”这类开放式负向条件；
10. 完成验证期间允许 State 静默变化。

#### 10.1.15 当前阶段结论

1. 完成条件是版本化契约，不是 Agent 的主观声明；
2. Completion Contract 应覆盖 Goal、Deliverable、State、Constraint、Evidence、Effect 和 Approval；
3. Required、Optional 和 Veto 必须分开；
4. Optional 不能抵消 Required 缺失，Hard Constraint 具有否决权；
5. Criterion 应支持 `SATISFIED / UNSATISFIED / UNKNOWN / NOT_APPLICABLE`；
6. Required Criterion 为 `UNKNOWN` 时不能干净成功；
7. 每个 Criterion 应有稳定 ID、Validator、Evidence 和 State Version；
8. State 变化后应使受影响的旧验证失效；
9. Contract 变更必须授权、版本化和可审计；
10. 条件化 Criterion 应由正式 State 决定是否适用；
11. 负向条件必须定义封闭检查范围；
12. 父任务应按 ALL、ANY、Quorum、Threshold 和 Child Role 聚合；
13. Join 只能接受当前有效 Attempt 的正式结果；
14. Partial Completion 必须由 Contract 预先允许；
15. 外部效果必须明确完成保证级别；
16. 完成验证应冻结 Candidate State Version；
17. 终态提交必须使用条件写；
18. LLM 可以参与语义判断，但 Control Plane 负责接受和提交；
19. 停止执行与任务完成始终是两个独立判断。

### 10.2 证据充分性

### 10.3 最大步骤与最大深度

### 10.4 最大 Handoff 次数

### 10.5 循环检测

### 10.6 无进展检测

### 10.7 边际收益停止

### 10.8 预算终止

### 10.9 人工终止

### 10.10 Graceful Degradation

## 11. Human-in-the-loop 运行语义

### 11.1 审批请求

### 11.2 澄清请求

### 11.3 人工反馈

### 11.4 人工接管

### 11.5 拒绝与超时

### 11.6 恢复执行

### 11.7 人工动作审计

### 11.8 人工不是异常兜底

## 12. 安全与治理

### 12.1 最小权限

### 12.2 动态能力授予

### 12.3 高风险工具审批

### 12.4 沙箱与执行隔离

### 12.5 Secret 传播

### 12.6 Prompt Injection 的跨 Agent 扩散

### 12.7 数据保留与删除

### 12.8 审计日志

### 12.9 Policy Enforcement

## 13. 可观测性

### 13.1 Run、Trace、Span、Event

### 13.2 任务图与执行图

### 13.3 模型输入输出

### 13.4 工具调用

### 13.5 路由与 Handoff 决策

### 13.6 状态转换

### 13.7 产物与证据关联

### 13.8 成本与延迟

### 13.9 失败与重试

### 13.10 隐私与脱敏

## 14. Agent 编排评测全景

### 14.1 Outcome Evaluation

### 14.2 Trajectory Evaluation

### 14.3 State Transition Evaluation

### 14.4 Tool-use Evaluation

### 14.5 Routing 与 Delegation Evaluation

### 14.6 Safety Evaluation

### 14.7 Reliability Evaluation

### 14.8 Cost and Latency Evaluation

### 14.9 Human Experience Evaluation

## 15. 结果评测

### 15.1 任务成功率

### 15.2 正确性、完整性与约束满足

### 15.3 证据与引用

### 15.4 副作用正确性

### 15.5 部分完成

### 15.6 可接受失败

## 16. 路径评测

### 16.1 正确路径不等于唯一路径

### 16.2 必要步骤覆盖

### 16.3 不必要步骤

### 16.4 工具和 Agent 选择

### 16.5 循环与重复

### 16.6 Handoff 质量

### 16.7 人工介入时机

### 16.8 路径级 Judge 的局限

## 17. 可靠性评测

### 17.1 故障注入

### 17.2 Retry 成功率

### 17.3 恢复成功率

### 17.4 重复副作用率

### 17.5 状态丢失率

### 17.6 超时与取消正确性

### 17.7 部分失败收敛

### 17.8 长运行稳定性

## 18. 评测集与测试方法

### 18.1 确定性单元测试

### 18.2 状态机属性测试

### 18.3 模拟模型与工具

### 18.4 场景测试

### 18.5 多轮环境评测

### 18.6 回放测试

### 18.7 对抗测试

### 18.8 线上 Canary 与 A/B

### 18.9 生产 Trace 抽样

## 19. 失败归因

### 19.1 模型能力问题

### 19.2 Prompt 与能力描述问题

### 19.3 路由和分解问题

### 19.4 上下文问题

### 19.5 工具接口问题

### 19.6 状态与运行时问题

### 19.7 权限和策略问题

### 19.8 评测器问题

## 20. 常见陷阱

### 20.1 只靠进程内状态

### 20.2 失败后无脑重新运行整条链

### 20.3 将重采样当作恢复机制

### 20.4 没有幂等键就重试副作用

### 20.5 Checkpoint 只保存聊天记录

### 20.6 无限等待人工

### 20.7 Trace 很全但无法归因

### 20.8 只用最终答案评测

### 20.9 LLM Judge 被同源偏差误导

### 20.10 忽略成本和尾延迟

## 21. 当前讨论顺序

Runtime 职责、统一执行身份，以及同步、后台、长运行与流式交付的边界已完成当前阶段讨论。§7 的 Timeout、Deadline、Heartbeat、Lease、Cancellation 与 Late Result 已提前深入完成；§8 已完成统一语义、§8.1 检查点内容和 §8.2 检查点时机，其余小节暂待后续；§10 已建立 Termination、Completion 与 Convergence 的统一模型并完成 §10.1 任务完成条件，下一步可进入 §10.2 证据充分性。Push / Pull、Queue / Actor / Workflow Runtime 和可重入执行留待后续按需要回补。主线后续按以下顺序继续：

1. 失败模型；
2. Retry 与 Idempotency；
3. 提交、补偿与事务边界；
4. Checkpoint、Pause 与恢复；
5. 并发、预算与终止；
6. Human-in-the-loop 与治理；
7. 可观测性；
8. 结果、路径和可靠性评测；
9. 评测集与失败归因。

## 参考资料

- [LangGraph：Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph：Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph：Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)
- [AWS Step Functions：Service integration patterns](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)
- [Azure Durable Functions：Orchestration versioning](https://learn.microsoft.com/en-us/azure/azure-functions/durable/durable-orchestration-versioning)
- [gRPC：Deadlines](https://grpc.io/docs/guides/deadlines/)
- [gRPC：Cancellation](https://grpc.io/docs/guides/cancellation/)
- [Kubernetes：Leases](https://kubernetes.io/docs/concepts/architecture/leases/)
- [OpenAI Agents SDK：Running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK：Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Microsoft Semantic Kernel：Agent Orchestration](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-orchestration/)
