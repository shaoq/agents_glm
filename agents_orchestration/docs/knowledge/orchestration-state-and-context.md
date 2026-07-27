# Agent Orchestration 知识点：状态与上下文（State and Context）

> **文档定位**：本文聚焦编排过程中的任务状态、执行状态、上下文、消息、事件、产物与所有权，回答「参与者之间传递什么、谁能看到什么，以及系统如何保持连续性和一致性」。
>
> **与其他知识文档的区别**：
> - [编排基础](agent-orchestration-foundations.md) 建立核心对象与三平面模型
> - [编排模式](orchestration-patterns.md) 讨论参与者如何协作
> - 本文讨论协作过程中状态和信息如何表达、流动与合并
> - [运行时、可靠性与评测](orchestration-runtime-reliability-evaluation.md) 讨论状态如何持久化、恢复、审计和评价
>
> **深入进度**：✅ §0 已深入 State、Context 与对话历史的核心边界；✅ §14 已深入 Orchestration State、Memory 与 RAG 的职责边界；🔄 主线下一阶段进入 §1 编排状态全景；评测内容统一后置到运行时、可靠性与评测文档。
>
> **更新日期**：2026-07-27

---

## 0. 核心认知

### 0.1 对话历史不等于编排状态

对话历史记录参与者过去交换了什么，但它不是系统正式承认的当前事实。

例如：

```text
Agent：“订单已经退款”
```

这只是一段模型输出。只有经过：

```text
动作提议
→ 权限与参数校验
→ 工具执行
→ 结果验证
→ State Commit
```

系统才能将订单状态正式提交为 `REFUNDED`。

对话历史通常无法可靠表达：

- 当前任务由谁拥有；
- 哪个 Attempt 仍然有效；
- 哪些 Step 已正式提交；
- 审批是否仍然有效；
- 哪个工具调用已经执行；
- 哪些内容只是候选意见；
- 当前使用的是哪个 Artifact 版本。

因此：

```text
Conversation History ⊂ Possible Context Sources
Conversation History ≠ Context
Conversation History ≠ State
```

对话历史可以是证据或 Context 来源，但不能替代正式状态机。

### 0.2 共享越多不等于协作越好

同一份正式 State 面向不同参与者，应生成不同的 Context：

```text
                    Canonical State
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
     Router Context   Finance Context   Evaluator Context
     能力标签          订单与退款策略      产物与验收标准
     请求摘要          必要客户信息        完成证据
     不含敏感数据      可用退款工具        不含执行权限
```

Router 不需要看到完整支付信息，Evaluator 不需要退款工具，普通 Worker 也不应获得用户身份凭证。

将全部消息、产物和运行时状态广播给所有 Agent，通常会带来：

- 敏感数据过度披露；
- 无关信息稀释当前任务重点；
- Prompt Injection 在参与者间传播；
- Token 和延迟成本增加；
- 不同版本和冲突信息同时出现；
- 不必要的工具和权限暴露。

上下文共享应遵循：

```text
Minimum Sufficient Context
最小充分上下文
```

即：既足以完成当前职责，又不超过该 Actor、Task 和信任边界所需的范围。

### 0.3 Context 是面向执行者的状态投影

#### 0.3.1 State 的工作定义

在 Agent 编排中，State 是：

> 在某个时间点，系统正式承认、可用于驱动后续控制决策的版本化事实集合。

这一定义包含四层含义。

第一，State 是正式承认的事实，而不是 Agent 的自然语言叙述、用户的单方面声明或未经验证的检索内容。

第二，State 能够驱动控制决策，例如：

- 下一步执行哪个节点；
- 哪个任务处于 Ready；
- 是否允许重试；
- 是否需要人工审批；
- 哪个 Actor 拥有任务；
- 是否允许执行副作用；
- 是否满足任务完成条件。

第三，State 必须具有版本或时间边界：

```yaml
task_id: task-001
status: RUNNING
owner: finance-agent
version: 17
updated_at: "..."
```

否则无法判断读取是否过期、审批针对哪个版本、并发写入是否冲突，以及恢复执行时应从哪里继续。

第四，State 不是一个简单的 `completed` 布尔值，而是多类正式状态的组合：

```text
Business State
Goal / Task State
Execution State
Control State
Artifact State
Approval State
Ownership State
```

例如：

```yaml
business:
  order_status: PAID

task:
  status: WAITING_FOR_APPROVAL

execution:
  current_attempt: attempt-3

control:
  next_action: refund_order

approval:
  request_id: approval-18
  status: PENDING

ownership:
  task_owner: finance-agent
```

#### 0.3.2 正式 State 不等于中央大对象

每一种正式事实都应有明确权威来源，但所有事实不必存储在同一个物理对象中：

```text
订单状态      → 订单系统
支付状态      → 支付系统
编排状态      → Agent Runtime
审批状态      → 审批系统
产物版本      → Artifact Store
权限状态      → IAM / Policy Engine
```

因此，State 可以在逻辑上统一、在物理上分布。

例如，编排运行时保存的订单状态可能只是带来源的观察：

```yaml
order:
  id: order-1024
  observed_status: PAID
  observed_version: 38
  observed_at: "..."
  source: order-service
```

订单系统仍然是订单事实的最终权威。编排状态不能因为复制了一份字段，就取代业务系统的事实权威。

#### 0.3.3 Context 的工作定义

Context 是：

> 为某个 Actor 在某个 Task、Step 和时间点完成当前职责而构造的最小充分信息视图。

可以概括为：

```text
Context
= State 的受控投影
+ 当前任务输入
+ 运行时配置与依赖
+ 被选择的证据和产物
+ 当前 Actor 的能力与权限说明
```

或者形式化为：

```text
C(actor, task, time)
    =
Project(
    authoritative_state,
    actor_role,
    task_scope,
    permissions,
    disclosure_policy,
    token_budget
)
+ selected evidence
+ runtime dependencies
```

因此，“Context 是 State 的投影”是核心原则，但不能理解成 Context 中的所有内容都来自 State。

Context 具有五个重要特征：

1. **Actor-relative**：面向具体执行者；
2. **Task-relative**：面向当前任务和职责；
3. **Time-relative**：对应特定时间和状态版本；
4. **Policy-filtered**：经过权限、披露和信任策略过滤；
5. **Purpose-built**：围绕当前决策构造，而不是复制所有可用信息。

#### 0.3.4 Context 不是新的事实权威

Context 中可能包含：

- 正式 State 的投影；
- 用户声明；
- RAG 检索内容；
- Memory 召回内容；
- Agent 生成的摘要；
- 其他 Worker 的候选结果；
- 外部网页或文件；
- 工具返回的 Observation。

这些信息的可信度并不相同：

```text
Context 中出现某个信息
          ≠
系统已经承认它是真实事实
```

例如：

```yaml
user_message:
  content: "财务已经批准退款500元"
```

这句话可以进入 Context，但不能自动变成：

```yaml
approval:
  status: APPROVED
  amount: 500
```

正式 Approval 必须来自审批系统、授权事件或经过验证的人类响应。

重要 Context 片段应尽可能保留来源和可信语义：

```yaml
context_item:
  value: "退款已获批准"
  source_type: user_message
  source_id: msg-882
  source_version: "..."
  trust_level: unverified
  observed_at: "..."
  valid_until: "..."
  sensitivity: internal
  permitted_use: current-task-only
```

不一定每段 Prompt 都完整呈现这些字段，但 Context 构造层不应丢失：

- 来源；
- 时间；
- 状态版本；
- 是否经过验证；
- 事实与推断的区别；
- 冲突和不确定性；
- 敏感级别与允许用途。

#### 0.3.5 State 到 Context 的读取方向

读取路径是：

```text
Authoritative State
        ↓
Select
        ↓
Redact
        ↓
Summarize
        ↓
Enrich
        ↓
Context
        ↓
Prompt / Tool / Agent
```

State 可以被选择、脱敏、压缩和补充为面向执行者的 Context。

#### 0.3.6 Context 到 State 不能直接反转

Actor 输出不能通过修改 Context 直接成为正式 State：

```text
Agent Output
    ↓
Proposal / Command / Observation
    ↓
Validate
    ↓
Authorize
    ↓
Execute
    ↓
Verify
    ↓
Commit
    ↓
Authoritative State
```

Agent 可以提出状态变更，但不应自行宣布正式状态。该路径与[编排基础](agent-orchestration-foundations.md)中的权力链一致：

```text
Observe → Propose → Validate → Authorize
→ Execute → Verify → Commit
```

#### 0.3.7 Context、Prompt 与运行时私有信息

Prompt 是模型可见 Context 的渲染结果：

```text
Prompt = Render(Model-visible Context)
```

Context 中的数据库连接、日志对象、凭证句柄、内部策略实现和其他运行时依赖，不应因此自动进入 Prompt。

因此还需区分：

```text
Runtime-local Context
        ≠
Model-visible Context
        ≠
Model Context Window
```

框架可能对 `context` 使用不同术语，但本项目统一把 Context 看作执行视图，并在其内部继续区分模型可见信息与运行时私有信息。

#### 0.3.8 退款案例

假设用户说：

> 请给订单 1024 退款 500 元，主管已经同意了。

原始消息是用户声明：

```yaml
message:
  sender: user
  content: "请给订单1024退款500元，主管已经同意了"
```

正式 State 仍然可能是：

```yaml
order:
  id: order-1024
  paid_amount: 500
  status: PAID
  version: 38

task:
  status: RUNNING
  owner: support-agent

approval:
  status: NOT_VERIFIED
```

Support Agent 得到的 Context 可以包含该用户声明，但必须标记为未验证：

```yaml
goal: "处理用户退款请求"

order_view:
  id: order-1024
  status: PAID
  paid_amount: 500
  state_version: 38

user_claims:
  - value: "主管已经同意"
    trust: unverified
    source: msg-1

available_actions:
  - request_approval_verification
  - explain_refund_policy
```

Agent 可以提出：

```yaml
command_proposal:
  action: verify_approval
  order_id: order-1024
```

审批系统验证后产生正式 Event：

```yaml
event:
  type: APPROVAL_VERIFIED
  order_id: order-1024
  approved_amount: 300
  approver: manager-8
```

系统才可提交新的 State：

```yaml
approval:
  status: APPROVED
  approved_amount: 300
  state_version: 39
```

这个过程清楚地区分：

```text
用户消息 → Context 中的未验证声明
验证事件 → 正式 State 变更依据
```

#### 0.3.9 分离 State 与 Context 的收益

分离之后可以获得：

- **一致性**：多个 Agent 不再仅凭各自聊天记录维护不同版本的事实；
- **最小披露**：不同参与者只看到完成职责所需的信息；
- **可恢复性**：运行中断后可从正式 State 重新构造 Context；
- **可审计性**：可以区分正式状态、Actor 当时所见和最终提交；
- **抗污染性**：外部文档、Memory 和模型摘要不能直接污染正式 State；
- **可替换性**：更换模型、Prompt 或 Agent 时无需改变正式业务状态模型。

本项目采用以下完整表述：

> Context 是针对特定 Actor、Task 和时间点，由正式 State、运行依赖及选定证据经过权限过滤和预算约束后构造的最小充分执行视图；它不是新的事实权威。

### 0.4 Message、Event、Command 与 Artifact 必须分开

### 0.5 任务所有权和写入所有权是并发安全的基础

### 0.6 状态变更必须可验证、可追踪、可恢复

从本轮核心边界可以先确立：

1. State 是系统正式承认的版本化事实；
2. 每类事实应有明确权威来源，但物理存储可以分布；
3. Context 是面向 Actor、Task 和时间点构造的最小充分视图；
4. Context 主要是 State 的投影，但也可包含运行依赖、证据和未验证信息；
5. Context 中出现的信息不自动成为事实；
6. Conversation History 只是 Context 来源之一，不是 State；
7. Prompt 是模型可见 Context 的渲染结果；
8. Agent 只能提出 State 变更，正式状态必须经过验证和提交；
9. Context 应保留来源、版本、时间、信任级别和敏感性；
10. Context 应能从正式 State 和明确来源重建，State 不能依赖某个模型的临时上下文才能解释。

## 1. 编排状态全景

### 1.1 业务状态

### 1.2 Goal 与任务状态

### 1.3 执行状态

### 1.4 控制状态

### 1.5 上下文状态

### 1.6 产物状态

### 1.7 权限与审批状态

### 1.8 状态之间的关联

## 2. Goal、Task、Attempt 与 Step

### 2.1 Goal：目标与成功判据

### 2.2 Task：可分配工作单元

### 2.3 Attempt：一次执行尝试

### 2.4 Step：运行时动作

### 2.5 父子任务与依赖

### 2.6 静态 DAG 与动态任务图

### 2.7 Task 身份与幂等键

### 2.8 完成证据

## 3. 任务状态机

### 3.1 状态不是一个简单 completed 布尔值

### 3.2 Pending、Ready、Running

### 3.3 Waiting、Blocked、Paused

### 3.4 Completed、Failed、Cancelled

### 3.5 Retryable Failure 与 Terminal Failure

### 3.6 状态转换合法性

### 3.7 状态转换原因与证据

### 3.8 父子任务状态聚合

## 4. Context 的定义与边界

### 4.1 Context 与 State

### 4.2 Context 与 Memory

### 4.3 Context 与 Prompt

### 4.4 Context 与 Artifact

### 4.5 当前工作上下文

### 4.6 依赖注入上下文

### 4.7 模型可见上下文与运行时私有状态

### 4.8 上下文生命周期

## 5. 上下文工程

### 5.1 最小充分上下文

### 5.2 上下文选择

### 5.3 上下文投影

### 5.4 上下文压缩

### 5.5 摘要与信息损失

### 5.6 结构化状态与自然语言叙述

### 5.7 指令、证据与不可信数据分层

### 5.8 Token 预算

### 5.9 Context Rot 与长期任务

## 6. 上下文隔离与披露

### 6.1 全局共享状态

### 6.2 Agent 私有状态

### 6.3 任务局部状态

### 6.4 用户与租户隔离

### 6.5 能力与权限隔离

### 6.6 敏感字段最小披露

### 6.7 跨信任边界传递

### 6.8 Prompt Injection 污染传播

## 7. Message、Command、Event、Observation

### 7.1 Message：通信内容

### 7.2 Command：请求发生动作

### 7.3 Event：已发生事实

### 7.4 Observation：环境反馈

### 7.5 Result：执行结果

### 7.6 自然语言与结构化载荷

### 7.7 消息身份、关联与因果关系

### 7.8 投递语义与重复消息

## 8. Artifact：产物优先的协作

### 8.1 消息协作与产物协作

### 8.2 Artifact 身份与类型

### 8.3 来源、版本与内容哈希

### 8.4 草稿、候选与已接受产物

### 8.5 证据与派生关系

### 8.6 大产物的引用传递

### 8.7 产物验证

### 8.8 生命周期与清理

## 9. 所有权模型

### 9.1 任务所有权

### 9.2 对话控制权

### 9.3 产物写入权

### 9.4 副作用提交权

### 9.5 单写者原则

### 9.6 租约与所有权超时

### 9.7 所有权转移

### 9.8 Handoff 与接管确认

### 9.9 所有权回收

## 10. 状态共享模式

### 10.1 共享黑板（Blackboard）

### 10.2 消息传递

### 10.3 Event Sourcing

### 10.4 Actor-local State

### 10.5 中央状态库

### 10.6 混合状态模型

### 10.7 各模式的适用条件与代价

## 11. 并发更新与冲突

### 11.1 乐观并发控制

### 11.2 悲观锁与租约

### 11.3 版本检查

### 11.4 Reducer 与可交换更新

### 11.5 Append-only 与覆盖更新

### 11.6 冲突检测

### 11.7 自动合并与人工裁决

### 11.8 重复执行与去重

## 12. Handoff 的状态传递

### 12.1 完整历史传递的代价

### 12.2 输入过滤

### 12.3 任务摘要与完成证据

### 12.4 权限重新计算

### 12.5 未完成动作与副作用

### 12.6 返回路径

### 12.7 身份与会话连续性

## 13. 结果聚合与知识保真

### 13.1 Aggregation 与 Synthesis

### 13.2 拼接、归约、排名、投票与裁决

### 13.3 冲突结果

### 13.4 少数意见

### 13.5 证据独立性

### 13.6 来源与引用保留

### 13.7 聚合生成的新主张

### 13.8 信息丢失检测

## 14. 与 Memory、RAG 的边界

编排 State、Memory 和 RAG 都可能被持久化、检索并放入 Context，但三者解决的是不同问题：

```text
Orchestration State：
当前这次任务正式进行到哪里？

Memory：
关于这个用户、Agent、项目或历史经历，过去有哪些可复用信息？

RAG：
外部知识语料中，有哪些文档证据与当前问题相关？
```

项目中已有独立的 [Memory Recall](../../../agents_memory/docs/knowledge/memory-recall-pipeline.md) 和 [RAG Query](../../../agents_rag/docs/knowledge/rag-query-pipeline.md) 知识文档。本节只定义编排侧与它们的接口边界，不重复展开其内部管线。

三者的总体区别是：

| 维度 | Orchestration State | Memory | RAG |
|---|---|---|---|
| 核心目的 | 协调当前运行 | 跨时间保持认知连续性 | 用外部知识支撑当前任务 |
| 主要组织中心 | Goal、Task、Attempt、Step | 用户、Agent、项目、实体、历史事件 | Document、Section、Chunk |
| 生命周期 | 当前任务或长运行实例 | 跨会话、跨任务 | 独立于具体任务长期存在 |
| 主要内容 | 正式状态、所有权、进度、审批、产物引用 | 偏好、事实、事件、经验、派生认知 | 文档事实、规范、手册、制度、技术资料 |
| 写入方式 | 状态机正式提交 | 受控抽取、查重和冲突判断后写入 | 文档采集、清洗、分块和索引 |
| 读取方式 | 按任务身份和版本直接读取 | 按当前任务触发召回 | 按 Query 检索和重排 |
| 默认权威性 | 当前编排事实的权威来源 | 历史认知证据，不自动代表当前事实 | 文档证据，权威性取决于来源与版本 |
| 进入 Context | State Projection | Memory Evidence | Retrieved Evidence |

可以将其概括为三种连续性：

```text
State 维持当前任务的执行连续性
Memory 维持主体的长期认知连续性
RAG 提供外部知识的证据连续性
```

### 14.1 编排工作状态与长期 Memory

#### 14.1.1 生命周期长短不是分类标准

不能简单理解成：

```text
短期数据 = State
长期数据 = Memory
```

一个 Agent 任务可能运行数周，它的 Task、Attempt、审批和所有权仍然属于 State。一条用户刚刚明确表达、但未来可复用的稳定偏好，则可能很快成为 Memory 候选。

边界取决于信息的职责，而不是保存多久：

```text
用于恢复当前执行位置      → State
用于未来任务恢复历史认知  → Memory
用于回答外部知识问题      → RAG
```

例如：

| 信息 | 归属 |
|---|---|
| 当前正在执行第 4 个 Step | State |
| 第 4 个 Step 已重试两次 | State |
| 用户长期偏好 Python | Memory |
| 上次项目选择了 Chroma | Memory / Event |
| Chroma 官方配置说明 | RAG |
| 当前生成的报告草稿版本 | Artifact State |
| 已发布的正式技术手册 | RAG Knowledge Base |

#### 14.1.2 Working Memory 的术语边界

在部分框架或认知模型中，Working Memory 可能指当前 Prompt、对话历史、Agent State、暂存中间结果或当前 Context。

为避免与 `agents_memory` 子项目中的长期记忆混淆，本项目统一使用：

```text
Working Context / Working State
→ 当前任务内的信息和状态

Long-term Memory
→ 跨任务可复用的主体历史信息
```

当前 Attempt 的临时变量、工具调用中间值、未完成计划和一次性文件路径，不应仅因为需要短期保存就进入长期 Memory。

#### 14.1.3 State 不能用 Memory 恢复

暂停任务必须从正式 State 或 Checkpoint 恢复：

```yaml
task:
  id: task-102
  status: WAITING_FOR_APPROVAL
  owner: finance-agent
  state_version: 18

attempt:
  id: attempt-3
  pending_action: refund-order-1024

approval:
  request_id: approval-9
  status: PENDING
```

不能仅召回一条：

```text
“上次似乎正在等待退款审批。”
```

Memory 可能经过摘要、内容不完整、已经过时、缺少精确版本，也无法表达幂等键和仍然有效的 Attempt。

因此：

```text
恢复执行位置 → State / Checkpoint
恢复历史认知 → Memory Recall
```

Memory 可以帮助解释任务背景，但不能替代执行恢复协议。

#### 14.1.4 Memory Recall 不是 State Restore

Memory Recall 返回的是带来源和边界的历史证据：

```yaml
memory_evidence:
  memory_id: memory-88
  content: "用户偏好使用 Python"
  subject: user-1
  type: fact
  valid_from: "..."
  confidence: 0.92
  source: message-27
```

其读取路径是：

```text
Memory
  ↓ Recall
Memory Evidence
  ↓ Context Assembly
Current Context
```

而不是：

```text
Memory Recall
  ↓
直接覆盖正式 State
```

召回的“用户偏好 Python”可以影响代码示例语言，但不能覆盖当前任务明确指定的 Java 要求：

```text
当前明确任务要求
    > 适用且已验证的长期偏好
    > 历史推断或派生认知
```

Memory 是认知证据，不是高优先级指令。

### 14.2 当前任务证据与 RAG 证据

当前任务可能已经拥有：

- 工具 Observation；
- 用户本轮提供的文件；
- 当前 Artifact；
- 业务系统实时查询结果；
- 已验证的审批 Event。

RAG 则可能检索到：

- 操作手册；
- 历史制度；
- API 文档；
- 公司知识库；
- 产品说明。

两者冲突时不能静默合并。例如：

```text
实时订单系统：
订单状态 = CANCELLED

RAG 检索到的历史报告：
订单状态 = PAID
```

对于订单当前状态，订单系统是领域事实权威，RAG 文档只是历史证据。

但在另一个场景中：

```text
Agent 自己推断：
最高退款金额 = 500

RAG 检索到当前正式退款政策：
最高退款金额 = 300
```

如果政策文档来源、版本和适用范围有效，它可能是政策领域的事实权威。

因此不能建立一条固定的全局优先级：

```text
State 永远高于 RAG
```

更准确的原则是：

> 权威性由事实所属领域、来源身份、适用时间和版本共同决定。

RAG 检索结果即使来自官方文档，也需要检查：

- 是否检索到正确段落；
- 文档是否为当前有效版本；
- 是否适用于当前用户、产品或地区；
- Chunk 是否丢失限定条件；
- 是否存在更新版本；
- Agent 的主张是否真正被原文支持。

Memory 和 RAG 可以同时进入 Context，但必须分槽：

```yaml
context:
  current_state: ...
  memory_evidence: ...
  rag_evidence: ...
```

不能将三者合成一段无来源摘要，否则执行者将无法区分当前事实、主体历史、外部文档和模型归纳。

### 14.3 何时召回 Memory

应在当前任务依赖主体历史时触发 Memory Recall，例如：

- 用户明确提到“上次、之前、还是、继续”；
- 当前任务需要用户偏好；
- 需要恢复历史决策；
- 需要利用过去成功或失败经验；
- 当前指代无法仅靠当前会话消解；
- 需要识别长期变化或历史事件链。

通常不需要召回的情况包括：

- 当前 Context 已经足够；
- 问题是通用知识；
- 当前任务明确要求忽略历史偏好；
- 历史信息不会影响当前决策；
- 召回范围无法满足用户、项目或租户隔离。

```text
每轮判断是否需要 Memory
          ≠
每轮都执行 Memory 检索
```

RAG 则适用于当前任务依赖外部知识语料时，例如：

- 查询产品或技术文档；
- 查找公司制度、法律、政策或业务规则；
- 引用用户提供的文档集合；
- 对知识库内容进行对比、总结或综合；
- 需要引用来源支撑主张。

以下场景通常不需要 RAG：

- 当前 State 和工具实时查询已经能回答；
- 只是恢复用户偏好；
- 只是恢复当前任务执行位置；
- 检索不会改变回答或行动。

Memory Recall 和 RAG Retrieval 都是 Context 构造的可选证据来源，而不是每次运行的固定步骤。

### 14.4 何时写入 Memory

Orchestration 不应把每次状态变化都同步成 Memory。推荐使用受控晋升流程：

```text
Message / Event / Artifact / Task Outcome
            ↓
      Memory Candidate
            ↓
   Eligibility Filtering
            ↓
Extraction / Redaction
            ↓
Deduplication / Conflict Resolution
            ↓
       Memory Commit
```

适合形成 Memory 候选的内容包括：

- 用户明确表达的稳定偏好；
- 跨任务可复用的个人或项目事实；
- 明确的历史事件；
- 已确认的项目决策；
- 可复用的经验教训；
- 经证据支持的派生认知。

不应直接写入：

- 每个 Step 的运行状态；
- 临时变量和工具过程；
- 未完成计划；
- 未验证的模型推断；
- 被否决的候选结论；
- 一次性文件路径；
- 未经治理的敏感信息；
- 没有未来复用价值的聊天内容。

尤其需要区分：

```text
Task Completed Event
        ≠
自动写入完整任务过程

Task Outcome
        ↓
抽取可复用候选
        ↓
受控 Memory Write
```

Memory 写入是知识晋升，不是运行日志同步。详细的抽取、查重、冲突判断和存储语义由 [Memory Write Pipeline](../../../agents_memory/docs/knowledge/memory-write-pipeline.md) 负责。

### 14.5 编排产物进入知识库的条件

Agent 生成 Artifact 不代表它已经成为组织知识：

```text
Artifact Draft
      ↓
Review / Verification
      ↓
Accepted Artifact
      ↓
Publication Decision
      ↓
RAG Ingestion
```

进入知识库前至少需要判断：

- 产物是否已经正式接受；
- 是否完成事实和引用验证；
- 是否有明确知识所有者；
- 是否适合跨任务复用；
- 是否已经脱敏；
- 访问控制如何设置；
- 哪个版本有效；
- 是否存在替代、撤回或失效机制；
- 是否保留原始来源和派生关系。

通常不应进入知识库：

- 中间草稿；
- 多个未裁决候选；
- 私有工作笔记；
- 未验证的 Agent 总结；
- 临时工具输出；
- 包含敏感 Context 的完整对话；
- 已被拒绝或取消的产物。

知识库写入同样是一种受控晋升：

```text
Accepted Artifact
       ↓
Knowledge Publication Gate
       ↓
Document Identity + Version
       ↓
Chunking / Indexing
       ↓
RAG Knowledge Base
```

#### 14.5.1 Memory 与 RAG 的重叠区域

例如：

> 项目 A 在 2026 年决定采用 PostgreSQL。

它既可以成为项目决策 Memory，服务后续协作连续性；也可以形成架构决策记录文档并进入 RAG 知识库，服务正式查询和引用。

区别不在文本内容，而在逻辑职责：

| 判断 | Memory | RAG |
|---|---|---|
| 主要用途 | 恢复主体历史认知 | 查询正式知识文档 |
| 主要身份 | subject / fact / event | document / section / chunk |
| 典型来源 | 交互、事件、历史行为 | 已发布文档 |
| 使用方式 | 个性化或连续性证据 | 可引用知识证据 |
| 生命周期 | 纠正、冲突、遗忘、失效 | 文档版本、替代、下架、重建索引 |

同一底层存储技术可以复用，但逻辑空间不能因此合并：

```text
同样使用 Vector Database
          ≠
Memory 与 RAG 是同一个系统
```

### 14.6 跨系统知识身份

State、Memory、Artifact 和 RAG 必须使用不同身份：

```text
task_id / attempt_id / event_id / artifact_id
memory_id / fact_identity / event_identity
document_id / document_version / chunk_id
```

不能因为两段文本相似就认为它们是同一个对象，也不能用 Embedding 相似度替代身份。

不同系统对象之间应通过显式关系连接：

```text
Memory M12
  └── derived_from Event E8

Document D4:v2
  └── published_from Artifact A9:v5

RAG Chunk C18
  └── part_of Document D4:v2

Artifact A9
  └── produced_by Task T3

State Transition S20
  └── supported_by Observation O7
```

常见关系包括：

- `derived_from`；
- `produced_by`；
- `published_from`；
- `part_of`；
- `cites`；
- `supersedes`；
- `corrects`；
- `describes`。

这些关系使系统能够在来源被纠正、删除或失效时，追踪哪些 Memory、Artifact 和 RAG 文档需要重新评估。

### 14.7 统一数据流

三者正确协作时：

```text
              ┌─────────────────────┐
              │ Orchestration State │
              │ 当前任务正式事实    │
              └──────────┬──────────┘
                         │ projection
                         ▼
┌──────────────┐   ┌──────────────┐   ┌───────────────┐
│ Memory Store │──▶│Context Builder│◀──│ RAG Knowledge │
│ 历史认知证据 │   │来源分槽与过滤 │   │ 外部文档证据  │
└──────────────┘   └──────┬───────┘   └───────────────┘
                          ▼
                     Agent / Tool
                          │
                 Proposal / Observation
                          ▼
                Validate / Execute / Commit
                          │
                          ▼
                Orchestration State Update
```

旁路写入则是：

```text
State / Event / Artifact
      ├── 可跨任务复用 ──→ Memory Candidate → Memory Write Pipeline
      │
      └── 可正式发布 ────→ Publication Gate → RAG Ingestion Pipeline
```

Memory 与 RAG 向当前任务提供证据，但都不能绕过正式状态提交协议。

### 14.8 当前阶段结论

1. Orchestration State 管理当前任务的正式执行连续性；
2. Memory 管理主体跨任务的历史认知连续性；
3. RAG 管理外部文档知识及其检索证据；
4. 生命周期长短不是三者的分类标准；
5. 当前任务不能仅靠 Memory 恢复；
6. Memory 和 RAG 的召回结果都是 Context Evidence，不自动成为 State；
7. 当前实时事实与文档、记忆冲突时，应按领域权威和有效时间裁决；
8. 状态变化不能自动同步成 Memory；
9. Artifact 必须经过接受、验证和发布 Gate 才能进入 RAG；
10. Memory 写入和知识库写入都是受控的知识晋升；
11. 同一信息可以同时形成 Memory 与正式文档，但身份和用途必须分开；
12. State、Memory、Document、Chunk 和 Artifact 必须拥有不同身份并保留派生关系；
13. Memory 与 RAG 可以共享检索技术，但不能合并语义和生命周期；
14. Context Builder 应分槽保留 State、Memory Evidence 和 RAG Evidence 的来源与信任边界。
## 15. 状态与上下文安全

### 15.1 不可信输入的传播

### 15.2 指令优先级

### 15.3 数据最小化

### 15.4 权限随 Handoff 重新评估

### 15.5 日志与 Trace 脱敏

### 15.6 状态删除与保留

## 16. 评测与诊断

### 16.1 状态转换正确率

### 16.2 上下文充分性

### 16.3 上下文污染率

### 16.4 Handoff 信息保留率

### 16.5 状态冲突率

### 16.6 重复执行率

### 16.7 聚合证据保真度

### 16.8 故障归因

## 17. 常见陷阱

### 17.1 把全部状态塞进 messages

### 17.2 所有 Agent 共享全部上下文

### 17.3 用自然语言代替状态机

### 17.4 没有任务和产物身份

### 17.5 Handoff 只转发聊天历史

### 17.6 多 Worker 直接覆盖共享产物

### 17.7 汇总时丢失来源和异议

### 17.8 将运行时状态错误写入长期 Memory

## 18. 当前讨论顺序

State、Context 与对话历史的核心边界，以及 Orchestration State、Memory 与 RAG 的职责边界已经完成当前阶段讨论。后续主线顺序为：

1. 编排状态全景；
2. Goal、Task、Attempt 与状态机；
3. Context 的定义、投影与上下文工程；
4. Message、Event、Command 与 Artifact；
5. 所有权与 Handoff；
6. 并发更新和冲突；
7. 结果聚合；
8. 安全与常见陷阱。

本文 §16 仅保留评测知识索引。状态转换正确率、上下文充分性、污染、冲突、重复执行和证据保真等评测主题，统一在[运行时、可靠性与评测](orchestration-runtime-reliability-evaluation.md)阶段讨论。

## 参考资料

- [LangGraph：Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [OpenAI Agents SDK：Context management](https://openai.github.io/openai-agents-python/context/)
- [OpenAI Agents SDK：Agents and context](https://openai.github.io/openai-agents-python/agents/)
- [OpenAI Agents SDK：Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [Microsoft Semantic Kernel：Agent Orchestration Advanced Topics](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-orchestration/advanced-topics)
