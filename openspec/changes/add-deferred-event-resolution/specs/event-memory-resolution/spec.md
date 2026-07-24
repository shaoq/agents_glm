## ADDED Requirements

### Requirement: 事件时间上下文
系统 SHALL 区分事件发生时间、消息发生时间和记忆存储时间，并 SHALL 使用可选的消息发生时间作为相对时间表达的参考。

#### Scenario: 有参考时间的相对表达
- **WHEN** event 来源消息带有 occurred_at 且内容包含“明天、昨天、上周”等相对时间
- **THEN** 系统 SHALL 基于该 occurred_at 生成带 start/end、granularity 和 timezone 的事件时间锚点

#### Scenario: 缺少参考时间
- **WHEN** event 包含相对时间但来源消息没有可用 occurred_at
- **THEN** 系统 SHALL 保留原始时间文本并标记 temporal anchor unresolved，且 SHALL NOT 使用当前系统时间猜测确定值

#### Scenario: 存储时间不是事件时间
- **WHEN** 用户在当前消息中描述过去或未来事件
- **THEN** memory created_at SHALL 表示写库时间，event temporal anchor SHALL 独立表示事件时间

### Requirement: 结构化事件框架
系统 SHALL 为 event 候选和持久化 event 记忆保存可选的结构化 EventFrame，至少能够表达参与者、谓词、对象、地点、状态、极性、模态和时间锚点；系统 MUST 将缺失字段表示为 unknown，而不是补造事实。

#### Scenario: 计划事件抽取
- **WHEN** 用户明确表达“我明天计划去北京”
- **THEN** 系统 SHALL 生成包含 user actor、travel predicate、北京目标、planned status 和明天时间锚点的 event 候选

#### Scenario: 信息不完整
- **WHEN** 用户只说“后来取消了”且当前证据无法确定对象或时间
- **THEN** 系统 SHALL 保留已知的 cancelled 状态和原始表达，并将无法确定的事件字段标记为 unknown

#### Scenario: 旧事件兼容读取
- **WHEN** 系统读取迁移前创建且没有 EventFrame 的 event 记忆
- **THEN** 系统 SHALL 将其事件结构视为 unknown/unresolved，且 SHALL NOT 拒绝读取该记忆

### Requirement: 多维事件关系
系统 SHALL 对候选 event 与相关历史分别判断 identity、temporal 和 semantic 维度，其中 identity SHALL 为 same_event、different_event 或 unknown；时间和内容关系不得单独充当事件身份。

#### Scenario: 不同时间的相似经历
- **WHEN** 两条 event 描述相同用户在明确不同时间进行相似活动
- **THEN** 系统 SHALL 将 identity 判定为 different_event，即使其文本高度相似

#### Scenario: 同一计划状态变化
- **WHEN** 新候选与历史具有兼容的参与者、目标和时间锚点，且状态从 planned 变化为 cancelled
- **THEN** 系统 SHALL 将 identity 判定为 same_event，并将内容识别为状态变化

#### Scenario: 身份证据不足
- **WHEN** 候选与历史内容冲突，但时间、对象或指代证据不足以判断是否为同一事件
- **THEN** 系统 SHALL 输出 identity unknown，并说明缺失维度

#### Scenario: 非法多维输出
- **WHEN** 关系模型输出未知 identity/temporal/semantic 枚举、未知 memory ID、重复 ID 或未覆盖输入历史
- **THEN** 系统 SHALL 拒绝该输出，并在有界格式修复失败后返回关系解析错误

### Requirement: 确定性事件动作
系统 SHALL 先依据事件身份、再依据内容关系生成事件动作，并 MUST NOT 将 `EVENT + CONTRADICT` 无条件映射为 ADD。

#### Scenario: 不同事件新增
- **WHEN** identity 为 different_event 且没有对目标历史的明确纠错
- **THEN** 系统 SHALL 执行 ADD，保持既有事件 active

#### Scenario: 同一事件重复
- **WHEN** identity 为 same_event 且 semantic relation 为 duplicate
- **THEN** 系统 SHALL 执行 NOOP，且 SHALL NOT 创建重复 event

#### Scenario: 同一事件状态演化
- **WHEN** identity 为 same_event 且候选表达计划取消、确认、进行或完成等状态演化
- **THEN** 系统 SHALL 执行 UPDATE，将旧版本标记 superseded、创建新 active 版本并保留来源历史

#### Scenario: 同一事件明确纠错
- **WHEN** identity 为 same_event 且来源明确纠正旧事件记录
- **THEN** 系统 SHALL 执行 UPDATE，将旧版本标记 retracted、创建新 active 版本并保留纠错来源

#### Scenario: 未知身份冲突
- **WHEN** identity 为 unknown 且候选与 active 历史形成 contradict，或纠错意图不足以安全定位目标
- **THEN** 系统 SHALL 执行 DEFER，且 SHALL NOT 猜测 ADD 或 UPDATE

### Requirement: 持久化待消解记录
系统 SHALL 将 DEFER 保存为独立 PendingResolution 真相记录，并 SHALL 保留候选、精确 scope、冲突目标、事件证据、缺失维度、来源、价值和生命周期状态。

#### Scenario: 创建 DEFER
- **WHEN** event 动作被判定为 DEFER
- **THEN** 系统 SHALL 持久化 open PendingResolution，且 SHALL NOT 创建 active memory、修改目标 validity 或写入 active Chroma collection

#### Scenario: 精确作用域隔离
- **WHEN** 后续 write 的 user_id、agent_id、session_id 与 pending item 的精确 scope 不同
- **THEN** 系统 SHALL NOT 将该请求的消息用于消解该 pending item

#### Scenario: 普通召回
- **WHEN** recall 查询 active memories
- **THEN** 系统 SHALL NOT 返回 PendingResolution 或其 deferred candidate

#### Scenario: 请求中存在无关安全候选
- **WHEN** 同一 write 同时包含一个 DEFER event 和与其无依赖的合法 fact/event
- **THEN** 系统 SHALL 在同一事务中保存 PendingResolution 和安全候选动作，且 SHALL NOT 因合法 DEFER 将整批标记为关系失败

#### Scenario: 相关候选成组延迟
- **WHEN** 同批其他 event 候选与 DEFER 候选共享冲突目标或事件框架重叠
- **THEN** 系统 SHALL 将其纳入同一待消解组，且 SHALL NOT 提前提交可能被该冲突否定的 active 事件

### Requirement: 后续写入静默消解
系统 SHALL 在每次后续 write 的候选抽取之后、普通候选决策之前，使用同 scope 的新原始消息和新候选尝试消解相关 open PendingResolution，并 MUST NOT 为此主动向用户提问。

#### Scenario: 原始消息提供指代证据
- **WHEN** 后续消息“对，就是明天那次”没有形成独立记忆候选，但能补足 pending item 的事件指代
- **THEN** 系统 SHALL 允许该原始消息参与消解

#### Scenario: 后续候选提供状态证据
- **WHEN** 新候选明确描述 pending 目标事件已经 cancelled
- **THEN** 系统 SHALL 消解为 same_event 状态变化，并生成 UPDATE 计划

#### Scenario: 确认是不同事件
- **WHEN** 新上下文提供不同时间或不同目标的充分证据
- **THEN** 系统 SHALL 消解为 different_event，并为 deferred candidate 生成 ADD 计划

#### Scenario: 没有新证据
- **WHEN** write 没有尚未处理的相关 evidence message
- **THEN** 系统 SHALL 保持 pending 状态，且 SHALL NOT 重复调用关系模型

#### Scenario: 消费本轮候选
- **WHEN** 本轮候选已作为 pending resolution 的充分证据并进入消解动作
- **THEN** 系统 SHALL 标记该候选已消费，且 SHALL NOT 在普通候选阶段再次 ADD 或 UPDATE

### Requirement: 消解时重新验证当前状态
系统 SHALL 将 PendingResolution 视为待判断事实而非延迟数据库命令，并 SHALL 在执行消解动作前重新读取目标及其当前 active successor。

#### Scenario: 目标仍然 active
- **WHEN** pending item 被消解且原冲突目标仍为 active
- **THEN** 系统 SHALL 基于当前关系生成并执行对应 ADD、NOOP 或 UPDATE

#### Scenario: 目标已发生变化
- **WHEN** 原冲突目标在等待期间已 superseded、retracted 或删除
- **THEN** 系统 SHALL 针对当前真相重新判断或将 pending 标记 obsolete，且 SHALL NOT 重放旧目标上的 UPDATE

### Requirement: 消解原子性与幂等
系统 SHALL 在同一 SQLite 事务中提交 pending 消解动作、当前请求安全动作、PendingResolution 状态、write request 和 index operation，并 SHALL 使相同新证据的重复处理结果不变。

#### Scenario: 成功消解并提交
- **WHEN** pending item 形成安全动作且本轮动作计划全部有效
- **THEN** 系统 SHALL 原子提交记忆变更并将该 pending item 标记 resolved

#### Scenario: 提交前失败
- **WHEN** 消解或当前计划在 SQLite 提交前发生不可接受错误
- **THEN** 系统 SHALL 回滚记忆与 pending 状态变更，且 SHALL NOT 操作 Chroma

#### Scenario: 重复证据
- **WHEN** 已处理过的 evidence message ID 因请求重试再次出现
- **THEN** 系统 SHALL NOT 重复创建记忆、重复转换目标状态或重复消解 pending item

#### Scenario: 索引同步失败
- **WHEN** SQLite 已提交消解结果但 Chroma 同步失败
- **THEN** 系统 SHALL 保留已提交报告和幂等 index operations，并允许既有 repair 机制收敛

### Requirement: 待消解生命周期维护
系统 SHALL 按可配置价值与 TTL 策略维护 PendingResolution，并 SHALL 在没有新事实证据时只执行生命周期操作，不得通过重复语义采样强行消解。

#### Scenario: 高价值待消解项
- **WHEN** pending item importance 达到高价值策略
- **THEN** 系统 SHALL 使用更长保留期并在相关 write 中优先匹配，但 SHALL NOT 主动向用户提问

#### Scenario: 到期未消解
- **WHEN** pending item 超过 expires_at 且仍无充分证据
- **THEN** 系统 SHALL 将其标记 expired 或按保留策略清理，且 SHALL NOT 创建 memory 或修改旧 memory

#### Scenario: 维护扫描没有新证据
- **WHEN** 定时维护扫描 open pending items 但没有新的来源消息
- **THEN** 系统 SHALL 只执行过期、重复合并、目标有效性检查和清理，且 SHALL NOT 重新调用关系模型

### Requirement: 待消解可观察性
系统 SHALL 在 WriteReport 和维护查询中区分 deferred、resolved、expired、obsolete 与技术失败，并 SHALL 提供不包含隐藏推断的判断依据。

#### Scenario: WriteReport 返回 DEFER
- **WHEN** 当前候选被安全延迟
- **THEN** WriteReport SHALL 包含 resolution ID、冲突 memory IDs、missing dimensions、reason 和 deferred 状态

#### Scenario: 查看待消解状态
- **WHEN** 运维调用方查询 pending resolution
- **THEN** 系统 SHALL 能按精确 scope 和状态查看数量、年龄、价值、最后评估时间和过期时间

#### Scenario: 技术失败与语义延迟区分
- **WHEN** 一个候选因证据不足 DEFER，而另一次请求因模型超时或索引不可用失败
- **THEN** 系统 SHALL 将前者报告为合法 deferred 结果，将后者报告为 FAILED 或 RETRYABLE
