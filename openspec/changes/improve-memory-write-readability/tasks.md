## 1. 建立行为基线与影响边界

- [ ] 1.1 在 `agents_memory` 运行完整测试与覆盖率检查，记录当前通过数量和写入相关模块覆盖率，确认重构基线无失败
- [ ] 1.2 分别对 `MemoryWritePipeline.write`、`PendingResolutionReconciler.reconcile`、`StorageCoordinator.commit` 和拟移动的事件匹配 helper 执行 GitNexus upstream impact，记录调用方、执行流和风险等级；HIGH/CRITICAL 时先暂停并报告
- [ ] 1.3 在 `tests/integration/test_write_pipeline.py` 增加批内 ADD 可见、UPDATE 目标失效、reconciliation candidate 消费和错误报告映射的特征测试，并确认测试在当前实现上通过
- [ ] 1.4 在 `tests/unit/test_reconciliation.py` 和 `tests/unit/test_coordinator.py` 补齐 Pending 生命周期、四种 Action 持久化、事务回滚及索引失败可修复的特征测试，并确认当前实现通过

## 2. 建立共享事件匹配与 DEFER 边界

- [ ] 2.1 为共享事件匹配写单元测试，覆盖缺失 EventFrame、无可比较维度、全部已知维度一致、任一维度冲突及 group 全量相关规则
- [ ] 2.2 新建 `src/agents_memory/processing/event_matching.py`，从 reconciler 提取无副作用的 `frames_related()` 和 `group_frames_related()`，保持比较字段和 unknown 语义不变
- [ ] 2.3 更新 `PendingResolutionReconciler` 使用共享事件匹配函数，删除对应私有实现并运行 reconciliation 测试
- [ ] 2.4 为 DeferredResolutionCollector 写单元测试，覆盖新建 group、按冲突目标合并、按事件相关性合并、无关候选分组、来源/processed evidence 去重、最高 importance 和 expires_at
- [ ] 2.5 新建 `src/agents_memory/processing/deferred.py` 实现 DeferredResolutionCollector，使其接收 policy 和显式时间、只构造领域对象且不访问 Repository
- [ ] 2.6 更新写入管线通过 collector 处理 DEFER，移除对 `reconciler._group_frames_related()` 的调用并运行 pending 与 write pipeline 测试

## 3. 显式表达批内工作视图

- [ ] 3.1 为 WriteBatchState 写单元测试，覆盖 plan/embedding 记录、ADD 暂存、UPDATE 目标淘汰、暂存记忆被后续替代及按 MemoryType 生成可见 histories
- [ ] 3.2 新建 `src/agents_memory/pipeline/state.py` 实现内部 WriteBatchState dataclass，不访问 Repository、模型服务或 coordinator
- [ ] 3.3 将 reconciliation plans 应用逻辑迁移到 WriteBatchState，保持 plan 顺序、candidate index、new_memory_id 和 embedding 映射
- [ ] 3.4 将普通候选规划迁移到 WriteBatchState，并运行批内多候选、ADD、UPDATE、NOOP、DEFER 集成测试验证 overlay 行为

## 4. 整理写入与 Pending 消解编排

- [ ] 4.1 为 `MemoryWritePipeline.write()` 各异常阶段补充参数化特征测试，锁定 extraction、relation、index、idempotency、request reservation、stale state 和 storage 的 WriteReport 映射
- [ ] 4.2 将 `write()` 提取为幂等检查、抽取处理、Pending 消解、reconciliation plan 应用、剩余候选规划和提交等有业务名称的私有步骤，保持公开签名和执行顺序
- [ ] 4.3 为 Pending 的候选证据路径、原始消息证据路径、无新证据路径和 active target 消失路径补充针对性测试
- [ ] 4.4 将 `reconcile()` 整理为新证据识别、生命周期计划、证据选择、断言组合、目标重载/决策和 Pending 更新步骤，保持公开签名、消费集合及 ActionPlan 语义
- [ ] 4.5 为写入入口和 reconciler 添加类/方法/阶段注释，说明 overlay、processed evidence、claimed targets、累积 assertion 与本轮 resolution evidence 的区别

## 5. 整理事务提交与存储导航

- [ ] 5.1 对 coordinator 测试增加 SQLite 事务 connection 贯穿内部动作且 helper 不独立提交的断言
- [ ] 5.2 将 `StorageCoordinator.commit()` 的 DEFER、NOOP、ADD/UPDATE、MemorySource 和 CandidateResult 构建提取为内部 helper，同时将唯一 transaction、request reservation、committed report 和 `_finish_sync()` 留在顶层可见
- [ ] 5.3 运行 coordinator、repository 和 write integration 测试，核对 UPDATE 的 RETRACTED/CORRECTS、SUPERSEDED/SUPERSEDES 以及 outbox 顺序未改变
- [ ] 5.4 为 `MemoryRepository` 增加 Memory、Source、Relation、Request、Index Operation 和 Pending Resolution 区段导航，并为 transaction、条件状态迁移、sweep/cleanup 补充不变量 docstring
- [ ] 5.5 检查新增注释，删除复述 Python 语句或逐行解释 SQL 的低价值注释，保留领域原因、边界与失败语义

## 6. 同步知识文档与完成验证

- [ ] 6.1 更新 `agents_memory/docs/knowledge/memory-write-pipeline.md` 的阶段图和职责说明，加入 WriteBatchState、DeferredResolutionCollector、共享事件匹配、reconciliation 步骤和单一 commit boundary
- [ ] 6.2 更新 `tests/test_architecture.py`，断言 `agents_memory` 不依赖兄弟子项目、写入管线不调用 reconciler 私有方法且新增模块未引入运行时第三方依赖
- [ ] 6.3 运行格式化、静态检查、`agents_memory` 完整单元/集成/架构测试及覆盖率检查，确认无网络依赖且结果全部通过
- [ ] 6.4 逐项核对 `memory-write-readability` spec 场景，比较公开方法签名、关键 WriteReport 序列化和 SQLite 状态，确认无外部行为或 schema 变化
- [ ] 6.5 提交前运行 `gitnexus_detect_changes()`，确认只影响预期符号和写入执行流，并检查 `git diff` 不包含其他子项目代码变更
