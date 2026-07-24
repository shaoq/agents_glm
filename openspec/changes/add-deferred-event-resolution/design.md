## Context

现有写入管线把候选分为 fact/event，再由 RelationResolver 输出 duplicate、supplement、contradict、correct 或 none，DecisionEngine 将关系映射为 ADD、UPDATE 或 NOOP。为了避免后续经历淘汰较早事件，当前规则把 `EVENT + CONTRADICT` 固定映射为 ADD。

该规则无法区分：

- 不同时间发生、可以共存的两个事件；
- 同一个计划从 planned 演化为 cancelled/completed；
- 同一个已发生事件的明确纠错；
- 缺少时间或指代信息、暂时无法确定是否为同一事件的冲突表达。

现有消息没有发生时间，`MemoryRecord.created_at/valid_from` 只表达存储生命周期，不能代表事件时间；RelationResolver 的历史输入也只有 id/content/type。项目要求继续保持 `agents_memory` 独立、SQLite 为真相源、Chroma 为可重建索引、精确 scope 隔离和默认无网络测试。

用户明确要求后台静默处理：系统不得为了消解事件身份主动向用户提问，只能利用后续自然对话提供的新证据。

## Goals / Non-Goals

**Goals:**

- 显式表达事件身份、时间关系和内容关系，停止由 `EVENT + CONTRADICT` 直接推导动作；
- 区分事件发生时间、消息发生时间和记忆创建时间；
- 在证据不足时安全 DEFER，不污染 active memory，也不错误淘汰历史事件；
- 在后续 write 收到自然上下文时静默消解相关 DEFER；
- 使消解动作、当前请求动作、pending 状态和索引日志原子提交、可幂等恢复；
- 允许维护流程对长期未决记录执行过期、去重、失效检查和清理；
- 保持既有 fact 行为、SQLite/Chroma 边界、精确 scope 和子项目独立性。

**Non-Goals:**

- 主动生成澄清问题或改变上层 Agent 对话；
- 引入独立 Event 实体表、知识图谱、本体或通用实体链接系统；
- 对所有自然语言时间进行复杂推理或保证完全归一化；
- 让 deferred candidate 参与普通 recall、用户画像或反思；
- 没有新事实证据时定时重复调用 LLM，靠采样漂移强行消解；
- 改造兄弟子项目或建立跨子项目代码、配置、存储依赖。

## Decisions

### 1. 采用结构化 EventFrame，不引入独立 Event 实体

event 候选携带结构化框架：

- actor；
- predicate；
- object；
- location；
- status（如 planned/confirmed/ongoing/completed/cancelled/unknown）；
- polarity；
- modality；
- temporal_anchor。

`temporal_anchor` 保存原始时间文本、start/end、granularity、timezone、certainty 和 resolution。Message 增加可选 `occurred_at` 作为相对时间参考；无参考时间时保留 raw text，并将 resolution 设为 unresolved，不使用当前系统时间猜测。

EventFrame 是判断证据，不是确定性 event hash。字段缺失、别名和上下文省略使纯哈希容易把同一事件拆开或把重复发生的事件合并。最终事件身份仍由结构化规则与 LLM 联合判断。

**替代方案：**

- 只增强 prompt：改动小，但历史没有结构化时间，输出难以校验；
- 独立 Event/Assertion 模型：表达最完整，但首期会扩展到实体合并和图生命周期，复杂度过高。

### 2. 分离三种时间

系统明确区分：

- `event_time`：事件计划或实际发生的时间；
- `message.occurred_at`：用户表达该信息的时间，也是相对时间参考；
- `memory.created_at/updated_at`：存储生命周期时间。

时间归一化使用半开区间和粒度表达兼容性，例如“2026 年 7 月”可表示为月区间。无法解析的时间仍是合法事件证据，但不得被当作精确时间参与同一事件的确定性判断。

### 3. 关系输出拆为 identity、temporal 和 semantic

event 关系结果包含：

- identity：same_event / different_event / unknown；
- temporal：same_window / before / after / overlap / unknown；
- semantic：duplicate / supplement / contradict / correct / none；
- explicit_correction、confidence、reason。

三个维度正交：不同事件可以文本冲突；同一事件可以是补充；内容重复也可能是不同时间的重复活动。解析层继续校验历史 ID 完整性、枚举合法性和一对多覆盖。

fact 继续使用既有语义关系，不要求构造 EventFrame。

### 4. Event 动作矩阵先看 identity

确定性规则如下：

| identity | semantic / evidence | action |
|---|---|---|
| different_event | 非明确纠错 | ADD |
| same_event | duplicate | NOOP |
| same_event | 状态演化或 contradict | UPDATE，旧版本 superseded |
| same_event | correct / explicit correction | UPDATE，旧版本 retracted |
| same_event | supplement | 首期保留为关联补充，不生成破坏性 UPDATE |
| unknown | contradict / correct 不充分 | DEFER |

`unknown + contradict` 不允许回退为 ADD 或 UPDATE。`correct` 只有在来源明确表达纠错且目标事件可定位时才执行；否则也 DEFER。

同一事件 supplement 的完整内容合并不在本变更范围。首期允许保存可追溯补充，但不得假装新候选是旧内容的完整替代。

### 5. DEFER 是合法领域动作，不是技术失败

新增 PendingResolution 真相记录，至少保存：

- resolution_id、精确 scope、候选及 EventFrame；
- 冲突 memory IDs 及判断时的关系证据；
- identity/semantic/temporal 结果；
- missing_dimensions 和 reason；
- source message IDs、processed evidence message IDs；
- importance、status、created_at、last_evaluated_at、expires_at。

DEFER 时：

- 不创建 active memory；
- 不修改目标 memory 的 validity；
- 不写入 active Chroma collection；
- 不进入普通 recall；
- 在 WriteReport 中显式返回 deferred 结果。

当前请求中的安全候选可以与 PendingResolution 在同一事务中提交。若同批其他 event 候选与该歧义候选共享目标或事件框架重叠，则将相关候选并入同一待消解组，避免提交一个已经可能被后续候选否定的计划。无关 fact 或 event 不因 DEFER 自动失败。

**替代方案：**

- 保持整批失败：最保守，但一个事件歧义会阻塞无关明确事实；
- 静默跳过：吞吐高，但重要变化永久丢失；
- 写入低置信 active：会污染 recall，不采用。

### 6. 后续 write 是语义消解的主入口

MemoryWritePipeline 在抽取和候选处理后、普通候选 ContextLookup 前执行 PendingResolutionReconciler：

1. 按精确 scope 读取 open pending items；
2. 用便宜条件按 EventFrame、时间、主题和近期性筛选相关项；
3. 将本轮原始 messages、抽取 candidates、pending candidate 和当前 active histories 作为证据；
4. 即使本轮零候选，也允许原始消息消解“对，就是那次”等依赖上下文的表达；
5. 只在出现尚未处理的新 evidence message ID 时重新语义判断；
6. 返回 resolution plans、仍 pending 的记录和已消费 candidate indexes；
7. 普通候选处理跳过已被消解流程消费的候选，并看到消解后的拟定 active 视图。

系统不得向用户发起澄清。高价值只影响保留期和后台匹配优先级，不改变对话行为。

### 7. 消解必须重新校验当前真相

PendingResolution 保存的是待判断事实，不是可延迟重放的数据库操作。等待期间目标 memory 可能已 superseded、retracted 或删除。

每次消解必须重新读取当前 active 状态并重新生成 ActionPlan；目标已变化时，沿当前 successor 重新判断或将 pending 标记 obsolete，不得直接执行旧 target 上的 UPDATE。

已消解动作、当前请求安全动作、PendingResolution 状态变更、write request 和 index operations 在同一 SQLite 事务提交。Chroma 继续在事务提交后按既有幂等日志同步。

### 8. 维护只管理生命周期，不在无证据时猜测

维护入口负责：

- 按价值策略和 TTL 将 pending 标记 expired；
- 合并同 scope 的重复 pending；
- 检查目标删除或失效并标记 obsolete；
- 清理已 resolved/expired 的保留记录；
- 输出 pending 数量、年龄、原因和消解率指标。

没有新 evidence 时不得重新调用关系模型。建议策略为：

- 高价值：长 TTL、相关 write 优先匹配；
- 普通价值：会话或配置 TTL 内等待；
- 低价值：短 TTL，到期放弃。

过期不创建 memory、不改变旧 memory。

### 9. Pending 与 active 召回严格隔离

PendingResolution 只存在于 SQLite 真相源，不进入普通 active 向量 collection。后续 reconciliation 可使用结构化预筛选，并在需要时使用独立的派生检索结构，但不得让普通 recall 把 deferred candidate 当作事实。

目标 active memory 在待消解期间保持原 validity。本变更只保留“存在 open conflict”的可观察关联，不改变 recall 排名；高风险 recall 如何利用 contested 信号留给召回能力单独设计。

### 10. schema migration 显式、向后兼容

SQLite schema version 递增并执行显式 migration。既有 event 记录没有 EventFrame 时按 unknown/unresolved 读取；既有 fact 行为不变。新增 Message 时间字段保持可选，旧调用方无需立即提供。

回滚时新代码写入的 pending 和事件扩展字段可保留在 SQLite；旧版本不得用于处理已迁移数据库。Chroma 可从 active memories 全量重建。

## Risks / Trade-offs

- **[EventFrame 抽取漂移]** → 严格 schema、保留 raw time、Fake 回归样例和真实模型评测集；结构化字段只作为证据，不直接哈希决定身份。
- **[每次 write 扫描 pending 增加延迟]** → 精确 scope、状态/TTL 索引、EventFrame 预筛选和有界候选数；仅相关项进入 LLM。
- **[长期 pending 膨胀]** → 价值分级 TTL、重复合并、resolved/expired 清理和指标告警。
- **[候选级 DEFER 改变整批失败语义]** → DEFER 作为显式成功结果进入 WriteReport；技术错误和非法关系仍回滚整个事务；相关 event 候选成组延迟。
- **[后续消息被重复作为证据]** → 持久化 processed evidence message IDs，并使 resolution transition 幂等。
- **[等待期间目标发生变化]** → 消解时回源当前 active 真相，不重放旧动作计划。
- **[旧 active 可能暂时过时]** → 不用未知候选破坏真相；记录 open conflict 供观测，召回侧 contested 策略另行设计。
- **[相对时间缺参考]** → 不用系统当前时间猜测，保留 unresolved 并等待后续自然上下文。

## Migration Plan

1. 增加 schema migration 和兼容读取，先支持 Message 时间、EventFrame、事件关系与 PendingResolution 数据；
2. 扩展抽取和关系模型，使用 Fake 覆盖时间解析、身份矩阵和非法输出；
3. 引入 DEFER 决策与持久化，但保持 deferred 与 active index 隔离；
4. 在 write pipeline 加入 reconciliation，并将消解、当前动作和 pending 状态原子提交；
5. 增加 pending sweep/cleanup 入口和可观察报告；
6. 对现有 fact、event ADD、NOOP、UPDATE、幂等、repair/rebuild 全量回归；
7. 使用固定真实模型样例验证相对时间、计划取消、不同事件、明确纠错和长期 unknown。

## Open Questions

TTL、EventFrame 预筛选阈值和高/普通/低价值的默认分界属于评测参数，不改变本设计契约；实施时提供配置默认值并通过样例调优。
