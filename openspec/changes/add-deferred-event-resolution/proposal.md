## Why

当前写入管线只用 `fact/event` 类型和单一语义关系决定动作，无法区分“同一事件发生状态变化”和“不同时间发生的两个事件”。当 event 与历史形成 contradict 且事件身份不明时，直接 ADD 会保留互斥 active 记忆，直接 UPDATE 又可能错误淘汰独立历史事件，因此需要一种不猜测、不打扰用户、可由后续自然上下文自动消解的安全机制。

## What Changes

- 为 event 候选和记忆增加结构化事件框架，包括参与者、谓词、对象、地点、状态、极性和独立于存储时间的事件时间锚点。
- 为输入消息提供可选发生时间，使“昨天、明天、上周”等相对时间能基于明确参考时间解析；缺少参考时间时保留原文并标记未解析。
- 将事件关系拆分为事件身份、时间关系和内容关系三个维度，避免由单一 contradict 直接推导 ADD 或 UPDATE。
- 扩展确定性动作矩阵：不同事件 ADD，同一事件按重复、状态变化或纠错执行 NOOP/UPDATE，事件身份未知且内容冲突时执行 DEFER。
- 新增持久化 PendingResolution；DEFER 不创建 active memory、不改变旧记忆，也不进入普通向量召回。
- 在后续每次 write 中，以同 scope 的新原始消息和新候选作为证据，在普通候选决策前静默重试相关 pending resolution；不主动向用户提问。
- 将已消解动作、当前请求动作和 pending 状态变更纳入同一 SQLite 事务，并在执行前重新校验目标记忆当前状态。
- 为长期未消解记录提供过期、失效、去重和清理入口；没有新证据时不得通过重复 LLM 调用强行得出结论。
- 同步扩充写入管线知识文档，记录事件身份、时间锚点、DEFER、后续上下文消解和维护职责边界。

## Capabilities

### New Capabilities

- `event-memory-resolution`: 定义事件框架、时间锚点、多维关系、DEFER 持久化、后续 write-time reconciliation、原子消解和后台过期行为。

### Modified Capabilities

无。`memory-write-pipeline` 当前仍属于尚未归档的在途 change；本能力在其基础上扩展事件语义，实施时必须保持既有 fact 写入、作用域隔离、幂等和双存储契约。

## Impact

- 影响 `agents_memory` 的消息/候选/关系/动作领域模型、事实抽取、关系解析、决策编排、SQLite schema、Repository、StorageCoordinator、WriteReport 和维护入口。
- write pipeline 将新增一次按精确 scope 检索相关 pending resolution 的前置消解阶段，但普通 recall 不得召回未决候选。
- SQLite 需要显式 schema migration；既有 memory 数据必须保持可读，新增事件字段允许旧记录以 unknown/unresolved 语义兼容。
- Chroma 仍是可重建派生索引；DEFER 记录不进入 active collection，已消解为 ADD/UPDATE 后才按既有 outbox 语义同步。
- 不新增兄弟子项目代码依赖，不引入主动用户澄清、独立 Event 实体、知识图谱或无限后台 LLM 重试。
