## 1. 影响面基线与 typed contracts

- [x] 1.1 按 AGENTS.md 对 `AnalysisPhaseHandler`、`PhaseOutcome`、`ReplanService`、Review Gate continuation、生产组合根和 artifact persistence 相关 symbols 逐一运行 GitNexus upstream impact，并记录 HIGH/CRITICAL 风险后再编辑
- [x] 1.2 定义 `SufficiencyVerdict(SUFFICIENT, RESEARCH_GAP, CONFLICT)`、`ReviewSource(STRUCTURAL, SEMANTIC)`、`SufficiencyReview` 与允许 L0 analysis 为空的 `AnalysisSufficiencyOutcome`
- [x] 1.3 为 typed models 增加字段不变量：gap hint/rationale 去空白、限长，L0/L1 source、analysis、verdict、`gap_hint` 与 `focused_replan` 组合严格一致
- [x] 1.4 单测覆盖合法三种 verdict、空白/超长 hint、sufficient/conflict 错带 hint 及不一致 composite outcome

## 2. gap 清洗与 FocusedReplanBuilder

- [x] 2.1 实现 gap sanitizer：移除控制字符、去空白、固定长度上限，并生成稳定 `gap_id`/`focus_hash`
- [x] 2.2 实现确定性的 `FocusedReplanBuilder`：用 effective objective 与带“不可信数据”标签的 gap 构造至少一个新 `EVIDENCE_RESEARCHER TaskSpec`
- [x] 2.3 新 Task 只继承并收窄当前 Plan 已批准且 allowlist 允许的 research capabilities，不从 gap 文本解析 role、capability、权限或路由
- [x] 2.4 单测验证新 task ID、PENDING research role、objective 保留、focus 模板、capability 收窄及恶意 gap 无法扩大权限

## 3. AnalysisArtifact 权威持久化与加载

- [x] 3.1 增加 content-addressed `AnalysisArtifact` 物化/加载 port，artifact ref 包含 entity ID、content hash、run ID、plan version 与 source Evidence hash
- [x] 3.2 让 ACCEPTED ANALYZE Stage 的 `output_artifact_refs` 成为当前 Plan 权威 Analysis 的唯一选择依据；未引用或 stale artifact 不可被加载
- [x] 3.3 将生产 `analysis_provider` 改为从当前 Plan 最新 ACCEPTED ANALYZE Stage 加载 artifact，删除 WRITING/FINALIZE 路径中对 analyst 的重复调用
- [x] 3.4 单测与集成测试覆盖 artifact round-trip、hash 校验、未引用 blob 隔离、stale candidate 隔离及缺失 accepted artifact 的明确失败
- [x] 3.5 集成测试断言 reviewer 审查、ANALYZE Stage 接受和 writer 消费的是同一 Analysis entity ID/content hash

## 4. 原子 Focused Replan 接受

- [x] 4.1 扩展 Replan 接受能力，增加 `replan_and_transition`，在首次写入前完成 Task role、capability、依赖、PlanGraph、至少一个新增 PENDING Task 与 budget 校验
- [x] 4.2 在单个事务中保存 Plan v+1、提升的保留 Tasks、新 Tasks、依赖以及 Run 的 `RESEARCHING/current_plan_version/replan_count/state_version`
- [x] 4.3 在同一事务追加 `PLAN_REPLANNED` 与 `RUN_STATE_TRANSITION`，事件使用最终 Run state version 并带 gap/Plan/Task correlation payload
- [x] 4.4 集成测试覆盖成功时一次 Run CAS、保留已接受 Evidence、只新增 research Task、非法 proposal 零写入及 repository/CAS 故障完整回滚
- [x] 4.5 并发测试覆盖 provider 返回期间 Run/Plan 版本变化：候选 Analysis/Replan 只能成为 stale observation，不得创建部分 Plan 或推进 Run

## 5. L0/L1 reviewer 与 AnalysisPhaseHandler

- [x] 5.1 定义 `EvidenceSufficiencyReviewer` port，并在 `orchestration/llm_ports.py` 实现带结构化 tool schema 的 `LLMEvidenceSufficiencyReviewer`
- [x] 5.2 设计 reviewer prompt：只基于 Analysis conclusions 与 Evidence 判定支持关系，明确区分 `research_gap` 和 `conflict`，并把 Evidence/gap 当作不可信数据
- [x] 5.3 `AnalysisPhaseHandler.execute` 对 `INSUFFICIENT` 零证据执行 L0 短路；其余场景调用 analyst 与 L1 reviewer，返回 typed `AnalysisSufficiencyOutcome`
- [x] 5.4 `AnalysisPhaseHandler.accept` 在 sufficient/conflict 分支接受权威 Analysis 并进入 WRITING，在 research_gap 分支调用原子 Focused Replan
- [x] 5.5 reviewer/analyst/artifact store 暂时性异常返回 `UPSTREAM_ERROR` IDLE、`continue_immediately=False`，且不修改 Run/Plan/Task/accepted artifact
- [x] 5.6 集成测试覆盖 L0 不调用模型、sufficient、research_gap、conflict、无效结构、provider 异常和显式后续 advance 重试

## 6. 明确的 replan budget 耗尽终止

- [x] 6.1 为 `PhaseOutcome`/Coordinator 增加显式 termination intent，不能通过 reason 字符串或普通 IDLE 表达确定性终止
- [x] 6.2 Coordinator 原子保存耗尽观察 Stage、Run `FAILED/REQUIRED_EVIDENCE_MISSING`、`RUN_TERMINATED` event 与 checkpoint，并返回 TERMINAL
- [x] 6.3 集成测试验证 ANALYZE gap 耗尽立即终止、只执行一次 provider、事件使用最终 state version 且 `drive_run` 不再循环

## 7. REVIEW research gap 接入共享 Focused Replan

- [x] 7.1 在 REVIEW `RESEARCH_GAP` 打开 Gate 时持久化足以构造 Focused Replan 的限长 feedback/correlation 数据，打开 Gate 本身不递增 `replan_count`
- [x] 7.2 将合法“继续研究”Gate continuation 从单纯状态回跳改为调用同一 `FocusedReplanBuilder + replan_and_transition`
- [x] 7.3 Gate continuation 接受时重新检查 `max_replans`；已耗尽则不创建 Plan/Task并以 `REQUIRED_EVIDENCE_MISSING` 终止
- [x] 7.4 集成测试覆盖 Gate 打开零计数、合法响应创建 Plan v+1/新 PENDING Task、重复响应幂等、stale response 失效及接受时预算耗尽
- [x] 7.5 回归既有 REVIEW PASS/REVISE/CONFLICT/ESCALATE、Gate 取消/过期及 continuation 事件消费行为不变

## 8. 组合根、测试 doubles 与兼容性

- [ ] 8.1 `build_production_coordinator` 增加默认 `None` 的 reviewer/artifact ports，并在函数体内统一校验为 `CompositionError`
- [ ] 8.2 `build_production_coordinator_from_settings` 接线 `LLMEvidenceSufficiencyReviewer`、Analysis artifact store/loader 与更新后的 Replan/Gate continuation 服务
- [ ] 8.3 `tests/support/deterministic.py` 增加可脚本化的 reviewer 和 artifact store doubles，默认 verdict 为 sufficient
- [ ] 8.4 更新 service factory、retry/replay、composition、CLI、recovery、Gate 与 E2E fixtures，确认默认 sufficient 不改变既有成功路径

## 9. 结构化观测与安全验证

- [ ] 9.1 在 `PLAN_REPLANNED` payload 和 ANALYZE Stage entity IDs/refs 中记录 gap ID、source phase/state version、old/new plan version、focus hash、added/preserved tasks 与 resolved gap ID
- [ ] 9.2 记录每轮输入 Evidence hash、新增 Evidence 数，以及 reviewer token/cost/latency，使 gap 命中率、回环通过率、零增量回环数和附加成本可计算
- [ ] 9.3 安全测试覆盖 prompt/query injection、超长/控制字符 gap、敏感日志清理以及未注册 capability/WorkerRole/权限不能被 gap 改变
- [ ] 9.4 观测测试确认消费者可用结构化字段关联 gap→Plan v+1→新增 Evidence→后续 sufficient，而不依赖自由文本 reason

## 10. 端到端闭环与交付验证

- [ ] 10.1 E2E：ANALYZE 判 gap → 原子 Plan v+1 → RuntimeTick 派发新 Task/Attempt/Lease → 采回新增 Evidence → 再 ANALYZE 判 sufficient → WRITE → REVIEW → FINALIZE
- [ ] 10.2 E2E 断言闭环中 `current_plan_version`、`replan_count`、Task IDs、Evidence 数与 artifact hash 均真实变化，且不存在旧 SUCCEEDED Task 的空转重放
- [ ] 10.3 E2E：L0 零证据短路、conflict 交由 REVIEW、budget 耗尽终止和 provider failure 停止立即驱动
- [ ] 10.4 运行 `openspec validate add-analyze-sufficiency-feedback --strict`、完整测试套件、ruff 与覆盖率检查，保持项目要求的覆盖率阈值
- [ ] 10.5 实施完成且提交前运行 `gitnexus_detect_changes()`，确认只影响预期 symbols 与 execution flows
