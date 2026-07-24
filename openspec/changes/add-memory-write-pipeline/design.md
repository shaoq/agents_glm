## Context

`agents_memory` 当前只有记忆写入、召回和维护的知识文档，没有包结构和应用代码。本变更落地首期写入核心闭环，完整实施设计见
`agents_memory/docs/specs/2026-07-24-memory-write-pipeline-implementation.md`。

约束如下：

- `agents_memory` 必须是独立 Python 子项目，不得导入、链接或运行时依赖 `agents_rag` 等兄弟子项目；
- 首期以本地单进程和 CLI 为交付形态；
- 事实抽取与语义关系判断使用 OpenAI 兼容 LLM，向量化使用 OpenAI 兼容 embedding；
- 写入必须支持来源归属、作用域隔离、批内去重、ADD / UPDATE / NOOP、历史有效性、幂等和部分失败修复；
- 后续召回与维护管线不在本变更实现，但会消费本变更形成的记忆真相。

## Goals / Non-Goals

**Goals:**

- 建立消息到持久化记忆的完整、可解释写入管线；
- 使用混合决策：LLM 理解语义，确定性逻辑执行边界和状态转换；
- 以 SQLite 保存完整真相，以 Chroma 提供可重建语义索引；
- 保留事实变化和纠错历史，同时保证默认当前视图只有 active 记录；
- 提供请求级幂等和跨存储失败后的确定性收敛；
- 通过依赖注入和 Fake 组件实现无网络默认测试；
- 提供 Python API 与 CLI 演示、查询、删除、修复入口。

**Non-Goals:**

- 召回评分、prompt 注入、遗忘、合并和反思；
- FastAPI、Web UI、消息队列、后台 worker 和分布式事务；
- 图谱、本体和复杂实体时间推理；
- 生产级权限系统与多节点并发协调；
- 复用兄弟子项目的源码、存储或配置。

## Decisions

### 1. 独立包，不建立跨子项目依赖

`agents_memory` 自己维护 `pyproject.toml`、`src/agents_memory`、`tests`、`.env.example`、README 和 `storage`。它可以与 `agents_rag` 选择相同的第三方库和工程模式，但不能导入其模块。

**原因：**

- 保持部署、测试和演进边界清晰；
- 避免 RAG 领域模型泄漏到 Memory；
- 后续任一子项目都可单独发布或替换依赖版本。

**替代方案：** 抽取共享基础包。首期不采用，因为目前只有少量形式相似的客户端和模型，提前共享会把两个领域耦合在尚未稳定的抽象上。

### 2. 模块化混合管线

写入过程拆为 FactExtractor、CandidateProcessor、ContextLookup、RelationResolver、DecisionEngine、MemoryRepository、MemoryIndex、StorageCoordinator 和 MemoryWritePipeline。

- LLM 负责抽取和关系理解；
- 规则负责字段校验、明确重复、scope、安全边界和动作映射；
- Pipeline 负责编排，不内嵌领域判断；
- 存储组件不反向依赖 Pipeline。

**替代方案：**

- 端到端单次 LLM 直接输出数据库操作：可控性、可测试性和安全性不足；
- 纯规则：无法可靠区分语义重复、补充和矛盾；
- 每个候选与每条历史逐对调用 LLM：调用量随 `N × K` 增长，且难以做一对多综合判断。

### 3. 每个候选一次整体关系判断

ContextLookup 返回同 scope、同 type 的 top-k active 历史。RelationResolver 每个候选只调用一次 LLM，在一个结构化响应中标注它与全部 top-k 的关系。

解析层必须拒绝：

- 输入中不存在的 memory ID；
- 未知关系枚举；
- 缺失或重复的关系项；
- 无法形成唯一安全动作的混合关系。

**原因：** 将复杂度控制在每批 `N` 次关系调用，同时让模型看到一对多上下文。

### 4. UPDATE 采用追加历史 + 当前有效性

UPDATE 是逻辑动作，不原地覆盖：

- contradict：旧记录转为 `superseded`，新记录为 `active`，建立 `supersedes`；
- correct：旧记录转为 `retracted`，新记录为 `active`，建立 `corrects`。

事件默认按时间序列 ADD，只有明确指出既有事件记录错误时才执行 correct。

**替代方案：**

- 静默覆盖：实现简单，但丢失变化来源和可审计性；
- 只追加不区分有效性：会使旧事实继续污染当前查重和后续召回。

### 5. 精确写入作用域

查重键为 `(user_id, agent_id, session_id, type)`。空的 Agent/Session 是明确作用域值，不表示向上或向下自动扩散。

**原因：** 写入阶段的错误合并不可逆且涉及隐私；后续召回可以显式聚合多层 scope，但写入不应隐式跨层更新。

### 6. SQLite 为真相源，Chroma 为派生索引

SQLite 保存：

- memories；
- memory_sources；
- memory_relations；
- write_requests；
- index_operations。

Chroma 只保存 `memory_id`、content、embedding 和检索过滤所需的最小 metadata。Chroma 命中必须回 SQLite 校验记录仍存在、scope 匹配且为 active。

**替代方案：**

- 仅 Chroma：精确事务、历史关系、来源与幂等状态表达能力不足；
- 仅 SQLite + 向量扩展：可降低一致性复杂度，但首期希望保留清晰可替换的语义索引接口，并验证双存储修复边界；
- 引入 PostgreSQL/pgvector：对本地首期过重。

### 7. SQLite 先提交，Chroma 同步修复

SQLite 单事务写入业务变更、请求快照和 `index_operations(pending)`；提交后同步执行 Chroma upsert/delete，再把操作标记为 synced 或 failed。

**原因：**

- SQLite 事务不能覆盖独立 Chroma；
- 先写真相确保失败后不会只剩无来源的向量；
- memory_id 上的 upsert/delete 可安全重放；
- 不引入消息队列也能提供明确恢复路径。

`index_operations` 首期是修复日志，不是异步 worker 的 outbox。正常路径仍同步执行。

### 8. 请求级幂等与原子动作计划

`request_id` 唯一，并绑定输入摘要：

- 已成功且已同步：直接返回保存的 WriteReport；
- SQLite 已提交但索引未完成：只修复索引；
- SQLite 提交前失败：允许重新执行；
- 相同 request_id 对应不同输入：拒绝。

所有候选先完成抽取、检索、关系解析和动作计划校验，再以单个 SQLite 事务提交。任一候选存在无法安全判断的歧义时，整批不落库。

**替代方案：** 候选级部分成功能提高吞吐，但调用方难以判断同一轮对话哪些事实已经生效，重试语义也更复杂。

### 9. 批内候选具有顺序可见性

确定性处理先移除完全相同候选。语义处理时，后续候选能看到前序候选的拟定 active 结果，避免首次空库时多个近义候选都得到 ADD。

不做激进的规则归一化；否定词、时间词和不确定性属于事实语义，必须保留。

### 10. 查重不可用时关闭写入

Chroma 在 ContextLookup 阶段不可用时，请求失败且不直接 ADD。

**原因：** 绕过查重会把临时基础设施故障转化为长期数据污染，后续维护成本高于一次可重试失败。

### 11. 依赖注入和 Fake 优先测试

Extractor、Embedder、Resolver、Repository 和 Index 均通过协议或抽象接口注入。默认测试使用确定性 Fake，不调用网络；集成测试使用临时 SQLite 和临时 Chroma。

真实模型测试仅作为显式手工 E2E，不进入默认 CI。

### 12. CLI 同时服务演示和运维

提供：

- `write`：消息 JSON → WriteReport；
- `list/show`：查看当前与历史；
- `delete`：带用户归属校验的删除；
- `sync repair/rebuild`：重放或重建派生索引。

CLI 同时支持人类可读输出和 JSON 输出，后者作为后续服务化的稳定观察面，但首期不承诺远程 API。

## Risks / Trade-offs

- **[LLM 抽取或关系判断错误]** → 使用严格结构化 schema、来源守卫、原子事实 prompt、一次修复重试和覆盖关键语义的回归样例。
- **[向量阈值漏掉隐含矛盾]** → 阈值只用于召回候选；保留可配置项和评测入口，后续由召回/维护管线持续发现。
- **[SQLite 与 Chroma 短暂不一致]** → SQLite 唯一真相、查询回源校验、持久修复日志、幂等重放和全量重建。
- **[同步 Chroma 增加写入延迟]** → 首期优先一致性和可观察性；出现真实吞吐证据后再引入后台 worker。
- **[请求级原子性降低局部成功率]** → 返回明确失败与可重试分类，优先保证一轮交互的整体语义一致。
- **[精确 scope 可能减少跨层去重]** → 首期优先隔离安全；跨层聚合留给显式召回策略。
- **[双存储增加工程复杂度]** → 将 Chroma 限定为可重建派生索引，不引入分布式事务。
- **[真实模型输出随时间漂移]** → 默认测试使用 Fake，真实 API 通过固定样例做手工验证，后续建立评测集。

## Migration Plan

该子项目尚无应用数据，不需要旧数据迁移。

1. 创建独立包、配置和空存储目录约定；
2. 首次运行时由 Repository 幂等创建 SQLite schema；
3. 创建带 embedding 模型与维度约束的 Chroma collection；
4. 先以 Fake 组件完成全链路测试；
5. 配置真实模型后执行手工 E2E；
6. 若部署失败，停止使用新 CLI 并删除可重建的本地测试存储即可回滚；不影响其他子项目。

后续 schema 演进必须引入显式版本号和迁移步骤，不依赖隐式建表覆盖。

## Open Questions

当前提案范围内无阻塞性开放问题。向量阈值、top-k 和具体 Flash 模型属于实现后的评测参数，不改变本设计契约。
