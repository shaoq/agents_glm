## Why

`agents_memory` 写入管线已经覆盖幂等、抽取、关系决策、延迟事件消解、事务提交和索引修复，但核心流程集中在少数长方法中，批内状态和领域不变量缺少明确命名，导致理解、评审和后续扩展的认知成本持续上升。现在需要在不改变既有行为的前提下，通过知识性注释和轻量内部抽象建立清晰、可验证的职责边界。

## What Changes

- 为写入管线、延迟事件消解和双存储提交补充面向领域知识的模块、类、方法及阶段注释，重点说明原因、不变量和失败语义，不复述显而易见的代码。
- 将 `MemoryWritePipeline.write()` 整理为可顺序阅读的阶段编排，并引入内部批次状态抽象，集中表达计划、嵌入、拟生效记忆、批内失效目标和延迟分组。
- 将 DEFER 分组从写入管线对 reconciler 私有方法的依赖中解耦，封装 PendingResolution 的创建、归组和证据合并规则。
- 将 Pending reconciliation 内部拆分为生命周期判断、证据选择、断言组合和重新决策等可独立理解的步骤。
- 在保持单一 SQLite 事务边界的前提下，将 `StorageCoordinator.commit()` 的 ActionPlan 分派、来源构建和结果构建整理为职责明确的内部操作。
- 为大型 Repository 增加职责导航和关键存储不变量；首期不引入通用 DAO 层，也不全面拆分领域模型文件。
- 使用现有回归测试与新增的特征测试证明重构前后公开 API、决策结果、持久化状态、幂等和索引修复语义一致。

## Capabilities

### New Capabilities

- `memory-write-readability`: 定义写入管线内部阶段、批内工作视图、DEFER 分组、Pending 消解和存储提交的可读性边界及行为兼容要求。

### Modified Capabilities

无。该变更只改善 `agents_memory` 内部结构和知识表达，不改变现有写入或事件消解能力的外部需求。

## Impact

- 主要影响 `agents_memory/src/agents_memory/pipeline/write.py`、`processing/reconciliation.py`、`storage/coordinator.py` 和 `storage/repository.py`，并补充相应单元与集成测试。
- `MemoryWritePipeline.write()`、`PendingResolutionReconciler.reconcile()`、`StorageCoordinator.commit()` 等现有公开入口及其输入输出保持兼容。
- Action、RelationKind、PendingResolution 状态机、SQLite schema、WriteReport、Chroma/outbox 同步语义均不改变。
- 不新增运行时第三方依赖，不修改 `agents_rag` 或其他兄弟子项目，也不建立跨子项目代码依赖。
