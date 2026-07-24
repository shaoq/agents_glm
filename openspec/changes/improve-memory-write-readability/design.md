## Context

`agents_memory` 是独立 Python 子项目。当前写入能力已经形成稳定的外部契约：`MemoryWritePipeline.write()` 负责请求级编排，`PendingResolutionReconciler.reconcile()` 在普通候选决策前利用新上下文消解历史 Pending，`StorageCoordinator.commit()` 将记忆、状态迁移、请求记录和索引操作原子提交到 SQLite，再同步可重建的 Chroma 索引。

复杂度主要集中在三个长方法：

- `MemoryWritePipeline.write()` 同时处理幂等、抽取、异常映射、Pending reconciliation、普通候选规划、批内可见状态、DEFER 分组和提交；
- `PendingResolutionReconciler.reconcile()` 同时处理生命周期、证据路由、事件断言组合、目标重载和重新决策；
- `StorageCoordinator.commit()` 同时处理 ActionPlan 分派、持久化、来源构建、关系迁移、outbox 和报告构建。

其中 `plans`、`embeddings`、`pending`、`inactive_ids` 和 `deferred_groups` 共同构成一个尚未提交的批内工作视图，但目前只表现为松散局部变量。写入管线还直接调用 reconciler 的 `_group_frames_related()` 私有方法，说明 DEFER 分组职责没有稳定边界。

本变更是可维护性重构。它必须保持现有测试所表达的业务行为，并遵守以下约束：

- `agents_memory` 不依赖 `agents_rag` 或其他兄弟子项目代码；
- SQLite 仍是真相源，Chroma 仍是可重建派生索引；
- DEFER 不创建 active memory、不进入普通索引；
- 同一请求中的确定性动作、Pending 状态和 index operation 仍在同一 SQLite 事务提交；
- 默认测试不访问外部网络。

## Goals / Non-Goals

**Goals:**

- 让顶层写入代码直接呈现从幂等检查到提交的业务阶段；
- 用明确类型表达批内拟生效和拟失效状态，降低多候选顺序处理的理解成本；
- 封装 DEFER 创建、分组和证据合并规则，消除跨对象调用私有方法；
- 让 Pending reconciliation 的生命周期、证据选择、断言组合和重新决策步骤可独立阅读与测试；
- 保持 SQLite 事务边界显式，同时降低 `commit()` 单个方法的分支密度；
- 用注释和知识文档记录关键原因、不变量、失败语义和职责边界；
- 使用特征测试证明公开结果和持久化副作用在重构前后保持一致。

**Non-Goals:**

- 修改 Action 决策矩阵、事件身份判断、时间锚点或 Pending 状态机；
- 修改公开 API、Pydantic 模型序列化格式、SQLite schema 或 Chroma collection；
- 引入工作流框架、通用 Command Bus、DAO 基类或每种 Action 一个独立服务；
- 全面拆分 `models.py` 或把每张 SQLite 表拆成独立 Repository；
- 改造 recall、maintenance 或兄弟子项目；
- 借可读性重构顺带改变错误恢复、性能策略或产品行为。

## Decisions

### 1. 采用“薄编排方法 + 小型内部领域对象”

`MemoryWritePipeline.write()` 保留现有公开签名和总入口职责，但只呈现以下阶段：

1. 计算输入哈希并处理已有请求；
2. 抽取和规范化候选；
3. 消解历史 Pending；
4. 将 reconciliation plans 应用到批内工作视图；
5. 规划未消费候选；
6. 交给 coordinator 提交；
7. 将阶段异常映射为 WriteReport。

阶段实现优先使用有业务名称的私有方法，不为每个步骤建立独立 service。重复的错误报告构建可集中为私有工厂，但异常类别到 `ErrorCode`、`WriteStatus` 和 `retryable` 的现有映射必须保持不变。

**替代方案：**

- 只加注释：改动最小，但无法消除局部状态耦合和私有边界泄漏；
- 为每个阶段建立 command/handler：结构最彻底，但当前规模下会产生大量跳转和样板代码。

### 2. 引入 `WriteBatchState` 表达事务前工作视图

新增内部 dataclass，集中持有：

- 按执行顺序排列的 `ActionPlan`；
- 以 candidate index 为键的 embedding；
- 本批次拟新增的 active `MemoryRecord`；
- 本批次拟淘汰的 memory IDs；
- 本批次创建或合并的 PendingResolution groups。

它提供有业务含义的操作，例如记录计划、暂存 ADD/UPDATE 产生的新记忆、从工作视图淘汰 UPDATE 目标，以及生成候选可见 histories。它不访问 Repository、不调用 LLM，也不提交事务。

该对象表达的是管线规划期 overlay，而不是新的持久化模型；不得加入 `models.py` 或暴露为公共 API。

**替代方案：**

- 保持多个列表和字典参数传递：文件数较少，但方法之间的数据契约隐含且容易遗漏同步更新；
- 在规划阶段直接写 SQLite：会破坏当前先规划、后原子提交的事务语义。

### 3. 将事件相关性规则提取为共享纯函数

从 reconciler 私有方法提取 `frames_related()` 和 `group_frames_related()` 到 `processing/event_matching.py`。该模块只依赖事件领域模型，不访问存储和外部服务。

`PendingResolutionReconciler` 和 DEFER collector 共同使用这些纯函数。这样既消除 `MemoryWritePipeline -> reconciler._private_method` 的依赖，也避免复制事件相关性算法。

首期保持现有比较字段和 unknown 规则完全不变；改进匹配算法属于独立行为变更。

### 4. 用 `DeferredResolutionCollector` 封装 DEFER 聚合

新增内部 collector，负责：

- 根据冲突目标交集或事件框架相关性定位已有 group；
- 创建新的 PendingResolution；
- 合并 grouped candidates、conflicting memory IDs、来源消息和 processed evidence IDs；
- 维护 importance、updated_at 和 expires_at；
- 返回写回 ActionPlan 的 PendingResolution。

collector 接收 `PendingResolutionPolicy` 和显式 `now`，避免测试依赖不可控时间。它只构造领域对象，不执行 Repository 写入。

### 5. Reconciler 先拆内部步骤，不引入多层服务

`reconcile()` 保留公开签名和返回类型，内部拆为有明确输入输出的步骤：

- 识别未处理的新消息；
- 计算过期或目标冲突导致的生命周期计划；
- 选择候选证据或原始消息证据；
- 组合累积 assertion 与本轮 resolver candidate；
- 重载 active targets 并生成重新决策计划；
- 更新 PendingResolution 的证据和状态。

现有 `_eligible_role()`、消息 ID 防复用和消息合并规则继续作为纯 helper。断言组合可使用小型结果 dataclass 命名“持久化断言”与“仅供关系判断的证据候选”的差异。

**替代方案：**

- 立即拆成 `EvidenceSelector`、`AssertionComposer`、`LifecyclePolicy` 三个公开类：边界看似清晰，但首期会放大内部 API；先通过私有步骤验证边界更稳妥。

### 6. Coordinator 保留一个显式事务，内部按动作分派

`StorageCoordinator.commit()` 继续拥有唯一的 `repository.transaction()`，请求预留、所有计划应用和 committed report 写入必须在同一个 `with` 块中可见。

事务内部可提取：

- DEFER plan 的保存与结果构建；
- NOOP plan 的保存与结果构建；
- ADD/UPDATE 的 MemoryRecord 和 MemorySource 构建；
- UPDATE 的 validity/relation 转换和 DELETE outbox；
- CandidateResult 构建。

helper 必须接收现有 transaction connection，不得自行开启、提交或关闭事务。事务提交后的 `_finish_sync()` 保持原位置和语义。

### 7. 注释解释领域知识，不复述语法

新增注释和 docstring 重点覆盖：

- 管线各阶段的输入、输出和失败边界；
- 批内 overlay 为什么必须让后续候选看到前序计划；
- Pending assertion 与本轮 resolution evidence 的区别；
- DEFER 与 active memory/index 的隔离；
- SQLite truth 与 Chroma derivative 的一致性恢复关系；
- UPDATE 如何根据 relation 选择 `RETRACTED/CORRECTS` 或 `SUPERSEDED/SUPERSEDES`；
- processed evidence、claimed targets 和精确 scope 的安全目的。

`MemoryRepository` 使用区段标题和关键方法 docstring 提供导航，不给直观 SQL 逐行加注释。同步更新 `docs/knowledge/memory-write-pipeline.md` 中的内部职责图，使知识文档与代码命名一致。

### 8. 以特征测试锁定行为，再进行结构调整

先补充覆盖以下组合的特征测试：

- 同批 ADD 后的候选可看到拟新增记忆；
- UPDATE 后的旧目标不再出现在后续候选 histories；
- 多个相关 DEFER 合并为同一 group；
- reconciliation 消费的候选不会再次普通处理；
- expired、obsolete、仍 open 和 resolved 的 Pending 状态；
- ADD、UPDATE、NOOP、DEFER 在 coordinator 中的持久化和结果；
- committed 但 index 未同步时仍可 repair；
- 重构后公开方法签名和序列化输出不变。

测试以 fake extractor/resolver/embedder/index 为主，不调用真实模型或网络。结构约束通过 architecture test 检查 `agents_memory` 不导入兄弟子项目，以及 pipeline 不调用 reconciler 私有方法。

## Risks / Trade-offs

- **[行为不变重构意外改变候选处理顺序]** → 先建立特征测试，`plans` 保持原追加顺序，逐阶段提交小改动。
- **[抽象增加文件跳转]** → 只为批内状态、共享事件匹配和 DEFER collector 建立新模块，其余优先保留为同文件私有方法。
- **[异常被 helper 过度统一后改变错误码]** → 为每个异常族锁定 WriteReport 特征测试，不引入通用 catch-all 映射表。
- **[时间相关测试不稳定]** → collector 和 reconciliation helper 显式接收或集中捕获 `now`，断言状态关系而非墙钟瞬时值。
- **[事务 helper 隐藏提交边界]** → `transaction()`、request reservation、committed report 和 `_finish_sync()` 继续留在 `commit()` 顶层。
- **[与在途 OpenSpec change 发生重叠]** → 本变更只在当前主干行为上重构；实施前确认 `add-memory-write-pipeline` 和 `add-deferred-event-resolution` 的交付状态，不修改其需求语义。

## Migration Plan

1. 运行现有 `agents_memory` 测试并记录基线，补齐写入、reconciliation 和 coordinator 特征测试；
2. 提取共享事件匹配纯函数并加入 DEFER collector；
3. 引入 `WriteBatchState`，逐段迁移 `write()` 的批内状态操作；
4. 将 `write()`、`reconcile()` 和 `commit()` 整理为阶段编排与内部 helper；
5. 增加领域注释、Repository 导航及写入知识文档；
6. 运行单元、集成、架构和覆盖率检查，并比较关键 WriteReport/SQLite 状态；
7. 使用 GitNexus 检查实际影响范围后提交。

无需数据迁移或部署切换。若重构验证失败，可按独立任务提交逆序回退；SQLite 数据和外部 API 没有迁移依赖。

## Open Questions

无阻塞问题。内部 helper 的最终精确命名可以在实施时依据测试和现有代码风格微调，但不得改变本设计规定的职责边界和兼容要求。
