## ADDED Requirements

### Requirement: 写入入口呈现稳定的业务阶段
系统 SHALL 保持 `MemoryWritePipeline.write()` 的公开签名和返回契约，并使其顶层控制流明确呈现幂等检查、候选抽取、Pending 消解、普通候选规划和事务提交阶段。

#### Scenario: 正常写入保持既有结果
- **WHEN** 相同 scope、messages、依赖输出和初始存储状态分别进入重构前后的写入管线
- **THEN** 两者产生等价的 ActionPlan 顺序、WriteReport 和持久化副作用

#### Scenario: 阶段失败保持既有错误语义
- **WHEN** 抽取、关系解析、索引查询或存储阶段抛出既有异常
- **THEN** 写入入口 MUST 保持对应的 WriteStatus、ErrorCode、retryable 和计数字段语义

### Requirement: 批内工作视图具有显式抽象
系统 SHALL 使用内部批次状态抽象集中管理动作计划、候选 embedding、拟新增 active memory、拟失效目标和 DEFER groups，并 SHALL NOT 将该规划期状态作为公共 API 或持久化模型暴露。

#### Scenario: 后续候选看到本批新增记忆
- **WHEN** 当前批次的前序候选计划为 ADD 或 UPDATE
- **THEN** 后续候选的关系判断 histories MUST 包含仍有效的拟新增记忆

#### Scenario: 后续候选看不到本批失效目标
- **WHEN** 当前批次的前序 UPDATE 已计划淘汰一个目标 memory
- **THEN** 后续候选的关系判断 histories MUST 排除该目标以及被同批后续 UPDATE 替代的暂存记忆

### Requirement: DEFER 分组具有独立领域边界
系统 SHALL 通过专门的内部组件完成 PendingResolution 的创建、相关 group 定位和证据合并，写入管线 SHALL NOT 调用 reconciler 的私有方法来判断事件分组。

#### Scenario: 相关歧义候选归入同一组
- **WHEN** 两个 DEFER 候选共享冲突目标或满足现有事件框架相关性规则
- **THEN** 系统 MUST 将候选、冲突目标、来源消息、processed evidence 和最高 importance 合并到同一 PendingResolution

#### Scenario: 无关歧义候选保持独立
- **WHEN** 两个 DEFER 候选既不共享冲突目标也不满足事件框架相关性规则
- **THEN** 系统 MUST 为它们保留不同的 PendingResolution

### Requirement: 事件相关性规则作为共享纯逻辑
系统 SHALL 为 Pending reconciliation 和 DEFER 分组提供同一套无存储、无网络副作用的事件框架相关性函数，并 SHALL 保持现有比较字段及 unknown 处理语义。

#### Scenario: 两个调用方得到一致判断
- **WHEN** reconciler 和 DEFER 分组组件接收相同的 EventFrame 组合
- **THEN** 两者 MUST 使用同一共享规则得到一致的 related 或 unrelated 结果

### Requirement: Pending reconciliation 具有可辨识步骤
系统 SHALL 在保持 `PendingResolutionReconciler.reconcile()` 公开契约的同时，分离新证据识别、生命周期判断、证据选择、断言组合、active target 重载和重新决策职责。

#### Scenario: 没有新证据时不重复判断
- **WHEN** 当前 messages 的 message IDs 均已记录在 processed evidence 中
- **THEN** reconciliation MUST 不调用关系模型且不生成重复状态迁移

#### Scenario: 新证据完成一次静默消解
- **WHEN** 新候选或新原始消息与 open Pending 相关且足以确定动作
- **THEN** reconciliation MUST 生成与现有逻辑等价的 ActionPlan、消费相应候选并更新 Pending 状态

#### Scenario: 生命周期终态保持兼容
- **WHEN** Pending 已过期、冲突目标已被同轮 claim 或目标不再 active
- **THEN** reconciliation MUST 分别保持现有 EXPIRED 或 OBSOLETE 状态及 NOOP 计划语义

### Requirement: 存储提交保持单一事务边界
系统 SHALL 允许 `StorageCoordinator.commit()` 将不同 ActionPlan 的处理拆为内部操作，但请求预留、SQLite 领域写入、Pending 状态、index operations 和 committed report MUST 保持在同一显式事务中。

#### Scenario: 任一计划写入失败时原子回滚
- **WHEN** 同一请求中的任一计划在 SQLite 提交前失败
- **THEN** 系统 MUST 不留下该请求的部分 memory、relation、pending、index operation 或 committed report

#### Scenario: SQLite 成功后索引失败仍可修复
- **WHEN** SQLite 事务成功而 Chroma 同步失败
- **THEN** 系统 MUST 保持 committed/retryable report 和可由既有 repair 流程重放的 index operations

#### Scenario: Action 语义保持不变
- **WHEN** coordinator 分别处理 ADD、UPDATE、NOOP 和 DEFER
- **THEN** MemoryRecord、MemorySource、validity transition、relation、PendingResolution、index operation 和 CandidateResult MUST 与重构前语义等价

### Requirement: 注释记录领域原因和不变量
系统 SHALL 为写入、Pending 消解和双存储协调的关键模块补充说明职责、阶段输入输出、失败边界和领域不变量的 docstring 或阶段注释，并 SHALL 避免对直观语句和 SQL 进行逐行复述。

#### Scenario: 阅读顶层模块可以识别关键约束
- **WHEN** 维护者阅读写入管线、reconciler、coordinator 和 repository 的入口与区段说明
- **THEN** 维护者 MUST 能识别批内 overlay、DEFER 隔离、processed evidence、claimed targets、SQLite truth 和 Chroma derivative 等关键约束

### Requirement: 知识文档与内部边界同步
系统 SHALL 更新 `agents_memory/docs/knowledge/memory-write-pipeline.md`，使其阶段图、职责说明和代码术语与重构后的内部抽象一致。

#### Scenario: 文档术语可以映射到代码
- **WHEN** 维护者从写入管线知识文档导航到实现
- **THEN** 文档中的写入阶段、批内工作视图、DEFER collector、reconciliation 和 commit boundary MUST 能映射到明确的代码职责

### Requirement: 子项目隔离和依赖集合保持不变
该变更 SHALL NOT 引入从 `agents_memory` 到 `agents_rag` 或其他兄弟子项目的代码依赖，也 SHALL NOT 为可读性重构新增运行时第三方依赖。

#### Scenario: 架构隔离检查
- **WHEN** 运行 `agents_memory` 架构测试和依赖检查
- **THEN** `agents_memory` MUST 仅使用自身代码、标准库和现有声明依赖
