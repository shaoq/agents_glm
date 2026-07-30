## Why

`EvidenceSet.sufficiency` 当前只影响最终 Completion Contract 评估，不参与进入 WRITING 前的路由；语义上的“现有证据不足以支撑分析结论”也只能在报告写完后由 REVIEW 发现。结果是系统可能先重复调用 analyst/writer，再通过人工 Gate 回到没有新任务的 RESEARCHING，既浪费模型调用，也无法形成真正的增量研究闭环。

## What Changes

- 新增 `EvidenceSufficiencyReviewer`：在 `AnalysisArtifact` 生成后，以 `AnalysisArtifact + EvidenceSet` 输出强校验的 `sufficient | research_gap | conflict` 结果；结构性零证据先由确定性 L0 分支短路，语义不充分再交给 L1 reviewer。
- `research_gap` 不再只是把 Run 状态改回 RESEARCHING，而是生成并接受一个 plan-scoped `FocusedReplanProposal`：创建 Plan v+1、至少一个新的 PENDING `EVIDENCE_RESEARCHER` Task，并把清洗后的 gap 作为该 Task 的聚焦查询上下文。
- Focused Replan 在单个事务中提交 Plan、Task、依赖、`current_plan_version`、`replan_count`、`RESEARCHING` 状态与 `PLAN_REPLANNED` 事件；任何校验必须在首次写入前完成。
- 不新增永久的 `Run.research_focus` 字段。研究 focus 绑定新 Plan/Task 生命周期，原始 goal/objective 不变，避免旧 focus 被后续 REVIEW 回环误用。
- 持久化通过充分性审查的 `AnalysisArtifact`，WRITING 必须读取同一个 artifact；不得通过 provider 再次调用 analyst 生成另一个 Analysis。
- `replan_count` 只在真实 Plan 版本变更时递增。ANALYZE 与 REVIEW 的研究缺口均通过同一 Focused Replan 服务消费共享的 `RunPolicy.max_replans`。
- `conflict` 不折叠为普通 research gap：保留既有 REVIEW/Gate 冲突语义；预判发现冲突时记录结构化结果并继续交由 REVIEW 裁决，不自动消耗 replan budget。
- replan budget 耗尽时直接以 `REQUIRED_EVIDENCE_MISSING` 确定性终止，不使用会重复调用 provider 的普通 IDLE。
- reviewer/provider 暂时性失败仍降级为不可立即重试的 IDLE；同一 fingerprint 由外部后续 advance 重试，不 crash Run。
- 在既有 Stage/Event 中记录 gap、Plan 版本、focus hash、新增证据量和后续是否通过，不新增 `EffectType`。

## Capabilities

### New Capabilities

- `analyze-sufficiency-feedback`: 定义 ANALYZE 前后的混合充分性检查、AnalysisArtifact 同一性、plan-scoped Focused Replan、共享回环预算、确定性耗尽行为及与 REVIEW 漏斗的边界。

### Modified Capabilities

<!-- openspec/specs/ 当前没有已归档的 orchestration lifecycle / analysis-report
     capability，因而没有可声明的 MODIFIED delta。本 change 依赖尚未归档的
     add-orchestration-run-coordinator、add-orchestration-llm-ports 与
     add-intelligent-research-orchestrator；归档时必须按依赖顺序合并契约，避免形成
     两份互相覆盖的 lifecycle baseline。 -->

## Impact

- **编排层**：`AnalysisPhaseHandler` 增加 L0/L1 判断、typed outcome 与自定义 accept；`ReplanService` 增加面向 phase 的原子“replan + transition”能力；REVIEW 的 `RESEARCH_GAP` Gate continuation 在确认回到研究时也必须提交真实 Focused Replan。
- **Artifact 交接**：增加可持久化/加载 `AnalysisArtifact` 的 port，WRITING、后续 REVIEW/FINALIZE 的 provider 从已接受 artifact 链读取，不再重跑 analyst。
- **LLM ports / 组合根**：新增 `LLMEvidenceSufficiencyReviewer` 并接入生产、确定性测试组合；缺失依赖由显式 `CompositionError` 校验。
- **领域模型**：新增带枚举 verdict 和字段不变量的 `SufficiencyReview`/`AnalysisSufficiencyOutcome`，不修改 `Run` JSON schema，不新增 SQLite Run 列。
- **安全**：gap hint 作为不可信数据清洗、限长并以标记字段写入 Task；不得改变 capability allowlist、WorkerRole、权限或路由。
- **回归面**：GitNexus 显示 `AnalysisPhaseHandler` 为 MEDIUM 风险；组合根、Replan、Gate continuation、artifact persistence、recovery 与端到端执行流均需覆盖。
- **依赖顺序**：本 change 以 `remove-noop-phase-tasks` 的“仅 RESEARCHING 调度研究 Task”约束为前提，并应在相关 orchestration changes 合并/归档后再归档。
