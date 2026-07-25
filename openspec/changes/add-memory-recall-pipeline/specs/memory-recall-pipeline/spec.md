## ADDED Requirements

### Requirement: Explicit recall service contract
系统 MUST 提供由调用方显式触发的 Recall 服务契约，接收当前查询、用户作用域、可选近期消息、显式限制和资源预算，并返回结构化 `RecallResult`。Recall 管线 MUST NOT 自行决定每轮对话是否应触发召回。

#### Scenario: Recall is invoked explicitly
- **WHEN** 调用方向 `MemoryService.recall(...)` 提交合法的 Recall 请求
- **THEN** 系统执行 Recall 管线并返回结构化结果
- **AND** 系统不要求调用方预先构造内部 `RecallIntent`

#### Scenario: Invalid request is rejected before retrieval
- **WHEN** 请求缺少 `user_id`、查询为空、时间范围非法或预算非法
- **THEN** 系统在访问向量索引或 LLM 前返回稳定的请求错误

### Requirement: Structured intent with deterministic fallback
系统 MUST 从当前查询、有限近期消息和调用方显式约束构建结构化 Recall Intent。身份与授权字段 MUST NOT 由 LLM 推断，模型推断 MUST NOT 覆盖用户显式约束。

#### Scenario: Intent is constructed from raw task context
- **WHEN** 请求包含当前查询和近期消息
- **THEN** 系统生成主要需求、有限查询变体、目标类型、时间需求和关系需求
- **AND** 系统区分显式约束与模型推断

#### Scenario: Intent model fails
- **WHEN** 意图模型超时、返回非法结构或低可信结果
- **THEN** 系统使用原始查询和显式约束生成保守意图
- **AND** 结果记录 `intent_fallback`

### Requirement: User-isolated hierarchical recall
系统 MUST 在同一 `user_id` 内支持当前会话、当前 Agent 历史会话和用户级共享三个候选通道。调用方 MUST 能关闭较宽通道，但任何配置 MUST NOT 扩大到其他用户。

#### Scenario: Three lanes are enabled
- **WHEN** 请求允许全部三个作用域通道
- **THEN** 系统分别生成 `session_current`、`agent_history` 和 `user_shared` 候选
- **AND** 每个候选保留命中通道

#### Scenario: Caller narrows the scope
- **WHEN** 调用方只允许当前会话
- **THEN** 系统不生成 Agent 历史或用户级共享候选

#### Scenario: Cross-user candidate is discovered
- **WHEN** 向量索引或关系边返回其他 `user_id` 的记录
- **THEN** 系统拒绝该记录
- **AND** 任何评分或 LLM 判断都不能恢复该记录

### Requirement: Multi-path bounded candidate generation
系统 MUST 通过有限的语义、结构化时间、未同步覆盖和一跳关系扩展路径生成候选，并 MUST 对每个通道、路径及全局候选数设置上限。

#### Scenario: Same memory is hit by multiple paths
- **WHEN** 同一 `memory_id` 被多个查询、通道或路径命中
- **THEN** 系统将其合并为一个候选
- **AND** 保留全部命中信号

#### Scenario: Temporal intent requires structured candidates
- **WHEN** 意图要求当前状态、指定时点、时间区间或状态演化
- **THEN** 系统从 SQLite 补充满足结构化时间条件的有限候选

#### Scenario: Active memory is not yet indexed
- **WHEN** SQLite 中存在作用域合法的 ACTIVE 记录但 Chroma 尚未同步
- **THEN** 系统通过有界未同步覆盖集使其能够参与本次 Recall
- **AND** Recall 不修改索引同步状态

### Requirement: SQLite is the recall truth source
系统 MUST 将 Chroma 仅作为候选索引，并在资格判断前从 SQLite 批量加载真实记录。SQLite 不可用时系统 MUST NOT 直接返回 Chroma 文档。

#### Scenario: Chroma contains a stale record
- **WHEN** Chroma 返回的 ID 在 SQLite 中不存在或已经失效
- **THEN** 系统排除该候选并记录陈旧索引诊断

#### Scenario: SQLite is unavailable
- **WHEN** 系统无法从 SQLite 验证候选状态和作用域
- **THEN** Recall 以不可恢复存储错误终止
- **AND** 不返回伪造的正常或空结果

### Requirement: Deterministic eligibility filtering
系统 MUST 在效用评分前执行记录完整性、用户边界、作用域授权、有效性、显式类型、硬时间限制和可消费性过滤。硬性拒绝 MUST NOT 被后续高分覆盖。

#### Scenario: Current-state query sees superseded memory
- **WHEN** 普通当前状态查询命中 `SUPERSEDED` 记录
- **THEN** 该记录不作为独立当前事实进入评分

#### Scenario: Historical query sees superseded memory
- **WHEN** 显式历史查询命中覆盖目标时点的 `SUPERSEDED` 记录
- **THEN** 该记录可以作为明确标记的历史候选继续处理

#### Scenario: Soft signal is weak
- **WHEN** 合法候选较旧、来自历史会话或重要性较低
- **THEN** 系统仅降低其软效用
- **AND** 不因此直接拒绝候选

### Requirement: Explainable memory utility scoring
系统 MUST 分别保留语义相关性、任务贡献、时间适配、作用域接近、可信度、命中稳健性和有界重要性等评分分量。综合分 MUST NOT 由单个软信号或 LLM 的不透明总分决定。

#### Scenario: Similar but unhelpful memory
- **WHEN** 候选向量相似度高但不能帮助当前任务
- **THEN** 任务贡献分量降低其综合效用

#### Scenario: LLM scoring fails
- **WHEN** LLM 批量复核失败
- **THEN** 系统继续使用确定性基础评分
- **AND** 结果记录 `scoring_fallback`

#### Scenario: Importance conflicts with relevance
- **WHEN** 高 importance 记忆与当前任务无关
- **THEN** importance 的有界贡献不能使其垄断排序

### Requirement: Temporal and relational evidence resolution
系统 MUST 区分事件时间、事实有效时间和记录时间，并 MUST 优先使用显式持久化关系、有效时间和结构化事件锚点解析当前、历史、演化、修正及取代证据。

#### Scenario: State evolves over time
- **WHEN** 两条记录描述同一属性在不重叠有效时间内的不同状态
- **THEN** 系统将其解析为 `EVOLVED`
- **AND** 不标记为未解决冲突

#### Scenario: Explicit correction exists
- **WHEN** 候选之间存在有效的 `CORRECTS` 关系
- **THEN** 修正记录作为主要证据
- **AND** 被修正记录不作为真实事实呈现

#### Scenario: Created time differs from event time
- **WHEN** 记录写入时间晚于其描述的事件时间
- **THEN** 系统使用事件或有效时间回答时点问题
- **AND** 不默认使用 `created_at` 代替事件时间

### Requirement: Conservative event identity and conflict handling
系统 MUST 在判断事件矛盾前评估是否为同一事件。关键冲突无法可靠裁决时系统 MUST 保留双方并表达不确定性，MUST NOT 猜测单一结论、创建 DEFER 或主动要求用户澄清。

#### Scenario: Events have clearly different anchors
- **WHEN** 两条相似事件在时间、地点、参与者或事件次数上明确分离
- **THEN** 系统将其视为独立事件而不是冲突

#### Scenario: Event identity is unknown and content differs
- **WHEN** 内容可能矛盾但证据不足以确认同一事件
- **THEN** 系统保留独立证据边界并标记 `UNKNOWN_EVENT_IDENTITY`
- **AND** 不持久化关系或待处理项

#### Scenario: High-value facts remain conflicting
- **WHEN** 两条高任务价值事实涉及同一槽位和相交时间且无法裁决
- **THEN** 系统将双方组成 `CONFLICTING` 证据组
- **AND** 上下文以中性方式表达双方

### Requirement: Set-level evidence selection
系统 MUST 以 `EvidenceGroup` 为选择单位，在数量和 Token 预算内综合直接价值、未覆盖需求、互补性、关系完整性、冗余和成本选择证据，而不是简单返回单条效用 Top-K。

#### Scenario: High-scoring memories are redundant
- **WHEN** 多个高分证据组提供相同信息
- **THEN** 系统优先选择能够增加需求覆盖的互补证据

#### Scenario: Conflict group exceeds the remaining budget
- **WHEN** 剩余预算不足以完整容纳关键冲突组
- **THEN** 系统先移除其他低边际信息或压缩格式
- **AND** 不只选择冲突的一方

#### Scenario: Evolution chain is long
- **WHEN** 状态演化链包含多个无实质变化的中间节点
- **THEN** 系统保留目标时点、当前状态和关键变化节点
- **AND** 省略无增量节点

### Requirement: Traceable structured result and context
系统 MUST 返回包含结构化 evidence、可注入 context 和 metadata 的 `RecallResult`。每个上下文条目 MUST 能追溯到证据 ID，组装过程 MUST NOT 把不确定关系改写成已确认事实。

#### Scenario: Recall finds sufficient evidence
- **WHEN** 选中证据能够直接覆盖主要需求
- **THEN** 系统返回 `SUFFICIENT`、结构化证据和预算内上下文

#### Scenario: Recall finds no eligible evidence
- **WHEN** 所有通道完整执行且没有合格证据
- **THEN** 系统返回空 context、空 evidence、`EMPTY` 和 `COMPLETE`

#### Scenario: Recall finds an unresolved key conflict
- **WHEN** 最终直接证据包含未解决关键冲突
- **THEN** 系统返回 `CONFLICTED`
- **AND** context 保留双方及不确定性说明

### Requirement: Explicit degradation semantics
系统 MUST 区分业务充分性与执行状态，并在 LLM、Embedding、Chroma、Tokenizer 或非核心通道失败时使用明确的有界降级路径。未验证候选 MUST NOT 因降级进入结果。

#### Scenario: Vector index is unavailable
- **WHEN** Chroma 查询失败但 SQLite 可用
- **THEN** 系统使用绑定用户、作用域和数量上限的 SQLite 候选回退
- **AND** 结果标记 `vector_index_unavailable` 和 `DEGRADED`

#### Scenario: One recall lane fails
- **WHEN** 一个非唯一通道失败而其他通道已完成资格验证
- **THEN** 系统可以返回其他通道的合法证据
- **AND** 记录失败通道，不将失败解释为无数据

#### Scenario: Tokenizer fails
- **WHEN** 精确 Token 计算器不可用
- **THEN** 系统使用保守字符估算
- **AND** 结果记录 `token_estimation_fallback`

### Requirement: Final consistency revalidation
系统 MUST 在组装结果前对最终选中记录执行轻量状态复核，且 MUST NOT 在等待 LLM 或索引期间持有 SQLite 长事务。

#### Scenario: Selected memory changes during recall
- **WHEN** 最终复核发现记录的有效性、关键时间或关系发生变化
- **THEN** 系统移除受影响证据组并最多重新选择一次

#### Scenario: State continues changing after reselection
- **WHEN** 一次重新选择后最终记录仍发生关键变化
- **THEN** 系统返回明确的并发修改错误
- **AND** 不无限重跑 Recall

### Requirement: Read-only recall behavior
Recall 主路径 MUST NOT 创建、更新、纠正、删除记忆，MUST NOT 更新索引状态、访问统计或 Deferred/Pending 状态。

#### Scenario: Recall completes normally
- **WHEN** Recall 返回任意充分性状态
- **THEN** 持久化记忆、关系、索引操作和待处理项均不因 Recall 被修改

### Requirement: Service, CLI, and configuration integration
系统 MUST 为同一 Recall Pipeline 提供 Python Service 和 CLI 入口，并 MUST 支持人类可读、JSON 和诊断输出。Recall 配置 MUST 只在构建 Recall Runtime 时校验。

#### Scenario: CLI recall succeeds
- **WHEN** 用户通过 CLI 提交合法查询和作用域
- **THEN** CLI 使用 `MemoryService.recall(...)` 的结构化结果渲染输出
- **AND** 不实现第二套 Recall 逻辑

#### Scenario: Recall is not configured
- **WHEN** 运行环境只执行既有存储维护命令且未配置 Recall 模型
- **THEN** 既有命令仍可启动和执行
- **AND** 只有调用 Recall 时返回未配置错误

### Requirement: Independent project and write compatibility
实现 MUST 保持 `agents_memory` 独立，不导入、读取或连接任何同级子项目的代码、环境文件、数据库或向量集合。新增 Recall 能力 MUST 保持现有 Write Pipeline 和写入查询契约兼容。

#### Scenario: Architecture dependency check runs
- **WHEN** 执行架构测试
- **THEN** Recall 和共享基础设施不存在对 `agents_rag` 或其他同级项目的运行时依赖

#### Scenario: Existing write tests run
- **WHEN** Recall 相关 Repository 和向量接口完成加法扩展
- **THEN** 现有 Write Pipeline、同步和维护命令测试继续通过
