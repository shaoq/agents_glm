## Context

当前管线为 `RESEARCH → ANALYZE → WRITE → REVIEW → FINALIZE`：

- RESEARCH Join 会计算 `EvidenceSet.sufficiency`。该结构性状态会在 FINALIZE 的 Completion Contract 中使用，但不会改变 pre-Writing 路由。
- `AnalysisPhaseHandler` 每次调用 analyst 后无条件进入 WRITING；生产组合中的 `analysis_provider` 又会重新调用 analyst，因此 ANALYZE 产生的对象并不是 WRITING 使用的对象。
- REVIEW 的模型输入目前只有 `ReportContent`，并不直接接收 `EvidenceSet`。`RESEARCH_GAP` 会打开既有 `CONFLICT_RESOLUTION` Gate。
- Gate continuation 或简单状态回跳只会让 Run 回到 RESEARCHING。由于旧 Plan 的研究 Tasks 已全部 SUCCEEDED，Research phase 会直接 Join 原证据并再次进入 ANALYZING，不会产生新的 Worker 派发。
- `ReplanService` 已能创建 Plan 新版本、保留已接受研究任务并新增 PENDING Task，但尚未与 ANALYZE/REVIEW gap 路径形成原子闭环。

本 change 必须同时解决“是否应补研究”“补什么研究”“如何产生可调度的新任务”以及“被审查对象是否被下游精确消费”四个问题，否则语义预判只会制造新的空转路径。

## Goals / Non-Goals

**Goals:**

- 用 L0 结构检查与 L1 语义检查在 WRITING 前识别研究缺口。
- 每个自动 research gap 都提交一个真实的 Focused Replan，并产生至少一个新的 PENDING 研究 Task。
- 让 gap 上下文绑定新 Plan/Task，而不是作为永久 Run 字段泄漏到后续迭代。
- 保证 WRITING 使用的 `AnalysisArtifact` 与通过充分性审查的对象完全一致。
- 让 ANALYZE 和 REVIEW 的真实 Replan 共享 `max_replans`，且计数只与 Plan 版本变更对应。
- 对预算耗尽、provider 失败、冲突、并发 stale 结果给出确定且可观测的行为。

**Non-Goals:**

- 不删除或瘦身 REVIEWING 的既有五种 verdict。
- 不改变 Completion Contract 的充分性判定规则。
- 不新增 `RunState`、`PhaseId`、`EffectType` 或 Gate 类型。
- 不允许 gap hint 决定 capability 权限、路由或 WorkerRole。
- 不把 Analysis/Report 的所有历史版本升级成通用内容管理系统；只建立本闭环所需的 accepted artifact 交接。

## Decisions

### 决策 1：使用 L0 + L1 混合漏斗

Analysis phase 读取当前 Plan 的 EvidenceSet 后按以下顺序处理：

1. `INSUFFICIENT` 表示 required research 下零独立证据，直接形成结构性 `research_gap`，不调用 analyst/reviewer。
2. 其他状态调用 analyst 生成候选 `AnalysisArtifact`。
3. L1 `EvidenceSufficiencyReviewer` 基于候选 Analysis 与 EvidenceSet 返回 `sufficient | research_gap | conflict`。
4. `sufficient` 与 `conflict` 都可进入 WRITING；`conflict` 只记录为需 REVIEW 继续裁决，不自动当成缺口补研究。

L0 避免零证据场景继续消耗两个模型调用；L1 负责“已有证据是否支撑具体结论”的语义判断。`CONFLICTED` 不被粗暴映射为 gap，避免把需要披露/裁决的矛盾误当成缺少资料。

### 决策 2：使用强类型、强不变量的 reviewer 结果

新增：

- `SufficiencyVerdict`: `SUFFICIENT | RESEARCH_GAP | CONFLICT`
- `SufficiencyReview(verdict, source, gap_hint, rationale)`，其中 source 为
  `STRUCTURAL | SEMANTIC`
- `AnalysisSufficiencyOutcome(analysis: AnalysisArtifact | None, review, focused_replan, source_evidence_hash)`

模型校验规则：

- `RESEARCH_GAP` 必须带去空白后非空、长度受限的 `gap_hint`。
- `SUFFICIENT`/`CONFLICT` 的 `gap_hint` 必须为 `None`。
- L0 `STRUCTURAL + RESEARCH_GAP` 的 `analysis` 必须为 `None`；所有 L1
  `SEMANTIC` 结果的 `analysis` 必须存在。
- `rationale` 同样限长，不得承载任意大模型原始输出。
- `focused_replan` 只允许出现在 `RESEARCH_GAP` 分支。

`PhaseOutcome.proposal` 携带一个 `AnalysisSufficiencyOutcome`，不在不同分支中复用互不相干的裸对象。

### 决策 3：research gap 必须创建 plan-scoped Focused Replan

新增确定性的 `FocusedReplanBuilder`。它输入当前 Run、当前 Plan、当前研究 Tasks 和经过清洗的 gap hint，输出 `ReplanProposal`：

- 保留既有已接受的 `EVIDENCE_RESEARCHER` Tasks/证据。
- 新增至少一个拥有新 `task_id` 的 PENDING `EVIDENCE_RESEARCHER` Task。
- 新 Task 的 description 由原始/effective objective 与带标签的 gap 数据组成；不得覆盖原始目标。
- 新 Task 的 capability 集合只能继承并收窄当前已批准研究能力，并再次通过 allowlist/PlanValidator 校验；gap 文本不能添加 capability。
- focus 天然绑定 Task 与 Plan v+1，不写入 `Run` 的永久字段。

`MultiSourceResearchHandler` 已把 `task.description` 用作 capability query，因此无需引入全局 focus 读取逻辑；测试必须证明新 Attempt 的 request query 来自新 Task，而不是旧任务重放。

### 决策 4：Replan、状态转换和事件必须原子提交

扩展现有 Replan 接受能力，提供面向 phase 的 `replan_and_transition`：

1. 在首次 repository 写入前完成 role、capability、依赖、PlanGraph 和 budget 的全部校验。
2. 创建并接受 Plan v+1。
3. 将保留任务提升到 v+1，保存新增 PENDING Tasks 与依赖。
4. 同一个 CAS 将 Run 更新为 `current_plan_version=v+1`、`replan_count+1`、`state=RESEARCHING`、`state_version+1`。
5. 同一事务追加 `PLAN_REPLANNED` 与 `RUN_STATE_TRANSITION`，两者使用最终 Run state version。

任一步失败必须回滚整个事务。`replan_count` 不再表示普通状态回环次数，只表示已提交的 Plan 版本变更。

REVIEW 的 `RESEARCH_GAP` 仍保留人工 Gate；当合法 Gate continuation 确认继续研究时，使用持久化的 Review feedback 调用同一个 Focused Replan 接受路径，而不是只把 Run 状态改为 RESEARCHING。由此 ANALYZE 与 REVIEW 才真正共享 `max_replans`。

### 决策 5：持久化并精确交接已审查的 AnalysisArtifact

候选 Analysis 在 provider 调用结束后以 content-addressed artifact 形式物化；只有 CAS 接受成功的 sufficient/conflict 分支，才通过 ACCEPTED ANALYZE Stage 的 `output_artifact_refs` 将其声明为当前 Plan 的权威 Analysis。

- WRITING 的 `analysis_provider` 必须按 run、当前 plan version 和最新 ACCEPTED ANALYZE Stage 加载该 artifact。
- 不得在 WRITING、REVIEW 或 FINALIZE provider 中重新调用 analyst。
- artifact 保存发生在 accept 之前时，CAS 失败留下的未引用 immutable blob 视为可回收孤儿；它不能被任何 provider 选为权威输出。
- gap 分支的候选 Analysis 仅作为观察材料，不成为 WRITING 输入。

测试以 artifact hash/entity ID 验证 reviewer 与 writer 消费同一个对象。

### 决策 6：预算耗尽确定性终止

当 verdict 为 `RESEARCH_GAP` 且 `replan_count >= max_replans` 时，PhaseOutcome 携带显式 `termination_reason=REQUIRED_EVIDENCE_MISSING`。Coordinator 在一个终止接受分支中原子保存：

- 当前 ANALYZE Stage 的观测结果；
- Run `FAILED` 与 termination reason；
- `RUN_TERMINATED` event/checkpoint。

AdvanceReport 返回 TERMINAL。该路径不返回普通 IDLE，因此不会在相同 fingerprint 下重复调用 analyst/reviewer。

### 决策 7：provider 失败暂停立即驱动，保留显式重试

analyst、artifact store 或 sufficiency reviewer 的暂时性异常返回：

- IDLE + `UPSTREAM_ERROR`
- `continue_immediately=False`
- Run/Plan/goal 不变

`drive_run` 在该点停止，后续由显式 `advance`/恢复操作重试。连续外部重试仍受既有 idle attempt budget 限制，但一次 drive 不会形成紧密 provider 重试循环。

### 决策 8：gap hint 是不可信、受限的数据

在构造 Task 前：

- 去除首尾空白、控制字符并限制长度。
- 用固定模板分隔 objective 与 `Research gap (untrusted data)`。
- 不解析 gap 中的 capability、WorkerRole、权限、工具或路由指令。
- 日志/事件只记录 `gap_id`、限长摘要和 `focus_hash`；敏感内容按既有日志清理策略处理。

PlanValidator、CapabilityRouter 与 allowlist 继续作为执行权限的权威边界。

### 决策 9：用现有 Stage/Event 建立可关联观测

不新增 EffectType：

- `PLAN_REPLANNED.payload` 记录 `gap_id`、`source_phase`、`source_state_version`、old/new plan version、focus hash、added/preserved task IDs。
- ANALYZE Stage 的 `output_entity_ids` 记录 verdict 与 gap correlation ID。
- 后续 sufficient Stage 记录其 resolved gap ID、输入 Evidence hash 和新增 Evidence 数量。
- 模型 usage ledger 记录额外 reviewer 的 token、cost、latency。

验收指标至少包括 gap 命中率、回环后通过率、每轮新增 Evidence 数、零新增证据回环数以及 sufficient 路径增加的成本/延迟。本 change 不预设净收益一定为正；上线评估需依据这些数据决定是否调整或关闭 L1。

### 决策 10：组合根缺失依赖使用显式校验

`build_production_coordinator` 的新 port 参数允许默认 `None`，进入函数后统一检查并抛 `CompositionError`；避免“规格承诺 CompositionError、Python 实际先抛 TypeError”的不一致。确定性 builder 默认注入 sufficient reviewer，保证旧测试行为不变。

## Risks / Trade-offs

- **[Focused Replan 扩大事务范围]** → 复用 ReplanService，所有验证前置，增加任意写入失败的事务回滚测试。
- **[额外 LLM 调用使 sufficient 路径变贵]** → L0 短路显然退化场景，记录 usage 与命中指标，以数据决定是否保留 L1。
- **[误判 gap 造成无价值研究]** → `max_replans`、新增 Evidence 观测和零增量告警共同约束；budget 耗尽立即明确终止。
- **[候选 artifact 在 CAS 冲突后成为孤儿]** → 只允许 ACCEPTED Stage 引用的 artifact 被下游读取，孤儿由后台清理。
- **[Review Gate continuation 改动影响既有 Gate 流]** → 保留 Gate 类型和 response vocabulary，只把合法“继续研究”响应的 continuation 从状态回跳替换为原子 Focused Replan。
- **[artifact handoff 触及生产组合的 MVP 假设]** → 限定为 AnalysisArtifact 的精确交接，同时为现有 report provider 重跑行为增加回归保护，不做无关内容平台重构。

## Migration Plan

1. 先增加 typed review/outcome、gap sanitizer、FocusedReplanBuilder 与单元测试，暂不改变 phase 路由。
2. 增加 AnalysisArtifact 的 content-addressed 持久化和 accepted-stage loader，切换 WRITING/FINALIZE 到权威 artifact，并验证同一性。
3. 扩展 ReplanService 的原子 `replan_and_transition`，接入 ANALYZE gap 与 REVIEW Gate continuation。
4. 接入 L0/L1 reviewer、显式终止分支与生产/确定性组合。
5. 增加结构化观测、全链路 E2E、并发 stale、事务回滚、安全和成本指标测试。
6. 回滚时关闭 L1 路由并恢复 sufficient 直通；已创建的 Plan/Artifact/Event 都是向后可读的历史记录，不删除数据。

## Open Questions

无阻塞性问题。实现期可以基于实际模型评测调整 prompt 和 gap/rationale 的长度上限，但默认上限、字段不变量和安全边界必须在代码中固定并可测试。
