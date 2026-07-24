## 1. 基线与影响分析

- [x] 1.1 运行现有 agents_memory 测试、ruff 和覆盖率，记录变更前基线
- [x] 1.2 对计划修改的领域模型、抽取器、关系解析器、DecisionEngine、MemoryWritePipeline、Repository 和 StorageCoordinator 逐一执行 GitNexus upstream impact，并记录风险
- [x] 1.3 固化 event identity、时间锚点、DEFER、静默消解和维护生命周期的验收样例

## 2. 领域模型与 schema migration

- [x] 2.1 先编写 Message 可选 occurred_at、TemporalAnchor、EventFrame、多维 EventRelation 和 DEFER 报告模型测试
- [x] 2.2 实现不可变事件模型、枚举和字段校验，并保持旧 Message/CandidateMemory 调用兼容
- [x] 2.3 先编写旧 schema 升级、旧 event 兼容读取和重复 migration 测试
- [x] 2.4 实现显式 SQLite schema version migration，以及 event 扩展字段和 pending_resolutions 表/索引

## 3. 事件抽取与时间锚点

- [x] 3.1 先编写计划、完成、取消、否定、绝对时间、相对时间和缺少参考时间的 EventFrame 抽取测试
- [x] 3.2 扩展 event 抽取 schema/prompt，使已知字段结构化、未知字段显式 unknown，且保留 raw time
- [x] 3.3 实现基于 message occurred_at 的有界时间归一化，并验证 created_at 不参与事件时间推断
- [x] 3.4 增加非法 EventFrame、强化不确定表达和虚构时间的拒绝/修复测试

## 4. 多维事件关系

- [x] 4.1 先编写 same_event、different_event、unknown、时间窗口关系和语义关系组合测试
- [x] 4.2 扩展 RelationResolver 输入，使候选与历史都携带 EventFrame、时间和必要来源证据
- [x] 4.3 实现 identity/temporal/semantic 结构化输出与 ID、覆盖性、枚举、置信度校验
- [x] 4.4 增加一次有界格式修复，以及证据不足必须保持 unknown 的回归测试

## 5. 确定性动作与候选分组

- [x] 5.1 先编写 different event ADD、same duplicate NOOP、状态演化 supersede、明确纠错 retract 和 unknown contradict DEFER 矩阵测试
- [x] 5.2 扩展 DecisionEngine，使 event 先按 identity、再按 semantic/evidence 生成动作，fact 既有矩阵保持不变
- [x] 5.3 先编写无关安全候选可提交、共享目标或重叠 EventFrame 候选成组 DEFER 的测试
- [x] 5.4 实现 DEFER 合法动作和相关候选分组，确保 deferred candidate 不进入批内 active overlay

## 6. PendingResolution 真相存储

- [x] 6.1 先编写 pending 创建、精确 scope 查询、状态转换、processed evidence 去重、过期和 obsolete 测试
- [x] 6.2 实现 PendingResolution Repository CRUD、状态机和精确 scope/状态/过期索引
- [x] 6.3 验证 DEFER 不创建 memory、不修改目标 validity、不生成 active index operation
- [x] 6.4 实现重复 pending 合并所需的稳定关联字段，并测试跨用户/Agent/Session 隔离

## 7. Write-time reconciliation

- [x] 7.1 先编写原始消息零候选仍可消解、新候选可消解、无新 evidence 不调用模型和无关 pending 不处理测试
- [x] 7.2 实现 PendingResolutionReconciler 的精确 scope 加载、结构化预筛选和新 evidence 守卫
- [x] 7.3 实现基于原始 messages、当前 candidates、pending candidate 和当前 active histories 的重新判断
- [x] 7.4 实现 resolution plans、consumed candidate indexes 和消解后拟定 active 视图
- [x] 7.5 将 Reconciler 接入抽取/候选处理之后、普通候选 ContextLookup 之前，并验证零候选路径

## 8. 原子提交、幂等与索引收敛

- [x] 8.1 先编写消解动作、当前安全动作、pending 状态和 write request 同事务提交/回滚测试
- [x] 8.2 扩展 StorageCoordinator，原子提交 resolved/expired/obsolete 状态及对应 ADD/UPDATE/NOOP/DEFER 结果
- [x] 8.3 先编写原目标已 superseded/retracted/deleted 时重新定位或 obsolete、禁止重放旧计划的测试
- [x] 8.4 实现提交前当前真相复核和 successor 重判边界
- [x] 8.5 验证相同 request/evidence 重试不重复创建记忆、转换 validity 或关闭 pending
- [x] 8.6 验证 SQLite 已提交而 Chroma 失败时，现有 repair/rebuild 能收敛消解产生的 index operations

## 9. 生命周期维护与可观察性

- [x] 9.1 先编写高/普通/低价值 TTL、expired、重复合并、目标失效和无证据维护不调 LLM 测试
- [x] 9.2 实现 pending sweep/cleanup 服务入口和可配置保留策略
- [x] 9.3 扩展 WriteReport，区分 deferred/resolved/expired/obsolete、技术 FAILED 和 RETRYABLE
- [x] 9.4 提供按精确 scope/状态查看 pending 数量、年龄、价值、最后评估和过期时间的 API/CLI 观察入口
- [x] 9.5 验证普通 list/show/recall 和 Chroma collection 不暴露 deferred candidate

## 10. 集成回归与文档

- [x] 10.1 添加消息 → EventFrame → 多维关系 → DEFER → 后续自然消息 → ADD/UPDATE 的端到端集成测试
- [x] 10.2 添加不同事件、计划取消、明确纠错、长期 unknown 到期和跨 scope 隔离集成测试
- [x] 10.3 回归既有 fact/event ADD、NOOP、UPDATE、批内去重、幂等、删除、repair 和 rebuild 测试
- [x] 10.4 更新 agents_memory README、配置示例和实现规格，说明 Message 时间、DEFER、静默消解和维护入口
- [x] 10.5 运行 pytest、ruff、覆盖率和 agents_rag 回归测试，并运行 GitNexus detect-changes 核对受影响符号/流程
- [x] 10.6 使用固定真实模型样例手工验证相对时间、same/different event、计划取消、明确纠错和不充分证据保持 DEFER
