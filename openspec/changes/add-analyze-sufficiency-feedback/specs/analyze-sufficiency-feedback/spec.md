## ADDED Requirements

### Requirement: ANALYZE 使用 L0 与 L1 混合充分性漏斗

系统 SHALL 在进入 WRITING 前判断当前 EvidenceSet 是否足以支撑 Analysis。required research 下零独立证据 MUST 由确定性 L0 分支直接判为 `research_gap`；其余场景 SHALL 在 analyst 产出候选 `AnalysisArtifact` 后调用 `EvidenceSufficiencyReviewer`，输出 `sufficient`、`research_gap` 或 `conflict`。

#### Scenario: 零独立证据由 L0 短路

- **WHEN** 当前 required EvidenceSet 的 `sufficiency` 为 `INSUFFICIENT`
- **THEN** 系统产生结构性 `research_gap`，且不调用 analyst、writer 或 L1 reviewer

#### Scenario: 语义充分进入 WRITING

- **WHEN** L1 reviewer 判定候选 Analysis 的结论得到当前 EvidenceSet 充分支持
- **THEN** verdict 为 `sufficient`，系统接受该 Analysis 并进入 WRITING

#### Scenario: 语义缺口进入 Focused Replan

- **WHEN** L1 reviewer 判定当前证据不足以支持候选 Analysis 的结论
- **THEN** verdict 为 `research_gap`，系统不得进入 WRITING，并进入 Focused Replan 接受流程

#### Scenario: 冲突不折叠为研究缺口

- **WHEN** L1 reviewer 判定现有证据或结论存在需要裁决的冲突
- **THEN** verdict 为 `conflict`，系统记录该结果、接受候选 Analysis 并继续进入 WRITING，且不自动递增 `replan_count`

### Requirement: reviewer 结果具有强类型字段不变量

系统 SHALL 使用枚举 verdict 和 `STRUCTURAL | SEMANTIC` source 表达充分性结果。`research_gap` MUST 携带清洗后非空且长度受限的 `gap_hint`；`sufficient` 与 `conflict` MUST 不携带 `gap_hint`。L0 structural gap MUST 不携带 Analysis，所有 L1 semantic 结果 MUST 携带 Analysis。不满足字段组合约束的 provider 输出 MUST 视为无效响应，不得改变 Run、Plan 或 Task。

#### Scenario: research gap 缺少 hint

- **WHEN** provider 返回 `research_gap` 但 `gap_hint` 为空、仅含空白或超过允许上限
- **THEN** 结果校验失败并按 `UPSTREAM_ERROR` 处理，Run、Plan 与 Task 均不改变

#### Scenario: sufficient 错带 hint

- **WHEN** provider 返回 `sufficient` 或 `conflict` 且同时携带 `gap_hint`
- **THEN** 结果校验失败，不接受候选 Analysis，也不进入下一 phase

#### Scenario: typed outcome 保持分支一致

- **WHEN** Analysis phase 构造 `AnalysisSufficiencyOutcome`
- **THEN** `analysis`、`review.source`、`focused_replan` 与 `source_evidence_hash` 的组合满足 L0/L1 和 verdict 对应的不变量，handler accept 不依赖 reason 字符串解析分支

### Requirement: research gap 创建可调度的 plan-scoped Focused Replan

系统 SHALL 为每个可继续的 `research_gap` 创建并接受一个 `FocusedReplanProposal`。该 Replan MUST 创建 Plan v+1，并至少增加一个新的 PENDING `EVIDENCE_RESEARCHER` Task；仅将 Run 状态改回 RESEARCHING 不满足本要求。

#### Scenario: gap 生成新研究任务

- **WHEN** verdict 为 `research_gap` 且 `replan_count < max_replans`
- **THEN** 当前 Plan 版本增加一，新 Plan 至少包含一个拥有新 task ID 的 PENDING `EVIDENCE_RESEARCHER` Task

#### Scenario: 已接受研究工作被保留

- **WHEN** Focused Replan 基于已有研究 Plan 创建新版本
- **THEN** 未被显式作废的既有研究 Tasks 及其已接受 Evidence 被保留，新任务只负责补充缺口

#### Scenario: gap 绑定新 Task 查询

- **WHEN** 新研究 Task 被 RuntimeTick 派发
- **THEN** capability request 的 query 包含原 objective 与带固定标签的清洗后 gap 数据，且该 query 来自新 Task description

#### Scenario: 原始目标保持不变

- **WHEN** Focused Replan 接受 research gap
- **THEN** Run 的 `raw_goal`、GoalSpec objective 和既有 Completion Contract 均保持不变

### Requirement: Focused Replan 在单次事务中原子接受

系统 SHALL 在首次持久化写入前完成新增 Task role、capability allowlist、依赖、PlanGraph 与 budget 的全部校验。Plan v+1、Task、依赖、Run 的 plan/state/counter 更新以及关联事件 MUST 在同一事务中提交；任何失败 MUST 回滚全部写入。

#### Scenario: 成功接受只执行一次 Run CAS

- **WHEN** 合法 Focused Replan 从 ANALYZING 被接受
- **THEN** Run 在一次以旧 `state_version` 为 expected 的 CAS 中同时更新为 `RESEARCHING`、`current_plan_version=v+1`、`replan_count+1` 和 `state_version+1`

#### Scenario: 校验失败不产生部分写入

- **WHEN** proposal 包含非 `EVIDENCE_RESEARCHER` Task、未允许 capability、无效依赖或无新增 PENDING Task
- **THEN** 接受失败，Plan、Task、依赖、Run counter/state 和事件均保持原样

#### Scenario: 事务失败完整回滚

- **WHEN** 任一 repository 写入或最终 Run CAS 失败
- **THEN** 新 Plan、提升后的旧 Task、新 Task、依赖与事件均不可见

#### Scenario: Replan 事件使用最终版本

- **WHEN** Focused Replan 成功提交
- **THEN** `PLAN_REPLANNED` 和 `RUN_STATE_TRANSITION` 使用最终 Run state version，并记录 old/new plan version、gap ID、focus hash、source phase 及 added/preserved task IDs

### Requirement: WRITING 使用通过审查的同一 AnalysisArtifact

系统 SHALL 将通过 `sufficient` 或 `conflict` 分支接受的 `AnalysisArtifact` 物化为 immutable artifact，并通过 ACCEPTED ANALYZE Stage 将其声明为当前 Plan 的权威 Analysis。WRITING、后续 REVIEW/FINALIZE provider MUST 加载该 artifact，不得重新调用 analyst 生成替代 Analysis。

#### Scenario: writer 消费同一 artifact

- **WHEN** reviewer 对 Analysis artifact A 返回 `sufficient` 且 Run 进入 WRITING
- **THEN** writer 接收到的 artifact entity ID 与 content hash 均与 A 相同

#### Scenario: gap 候选 Analysis 不成为写作输入

- **WHEN** reviewer 对候选 Analysis 返回 `research_gap`
- **THEN** 该候选 Analysis 不被标记为当前 Plan 的 accepted Analysis，后续 Plan 的 WRITING 不得加载它

#### Scenario: stale Analysis 不被接受

- **WHEN** provider 调用期间 Run state version 或 Plan version 发生变化
- **THEN** 候选 Analysis 与 review 仅作为 stale observation 保存，不得成为 ACCEPTED Stage 输出或推进 Run

#### Scenario: 未引用 artifact 不具权威性

- **WHEN** artifact blob 已物化但 phase CAS 失败
- **THEN** 下游 provider 不能加载该未被 ACCEPTED Stage 引用的 blob，其后可由孤儿清理回收

### Requirement: ANALYZE 与 REVIEW 的真实 Replan 共享预算

系统 SHALL 仅在成功提交新 Plan 版本时递增 `replan_count`。ANALYZE 的自动 gap 与 REVIEW `RESEARCH_GAP` Gate 确认后的 continuation SHALL 使用同一 Focused Replan 接受路径和同一个 `RunPolicy.max_replans` 上限。

#### Scenario: ANALYZE Replan 消耗一次预算

- **WHEN** ANALYZE gap 成功提交 Plan v+1
- **THEN** `replan_count` 恰好递增一次

#### Scenario: 打开 Review Gate 不提前消耗预算

- **WHEN** REVIEW 产生 `RESEARCH_GAP` 并打开 Gate
- **THEN** 打开 Gate 本身不修改 Plan 版本或 `replan_count`

#### Scenario: Review continuation 提交真实 Replan

- **WHEN** 合法 Gate response 确认继续研究且 budget 尚未耗尽
- **THEN** continuation 根据已持久化的 Review feedback 创建新 PENDING 研究 Task，并原子提交 Plan 新版本、`RESEARCHING` 状态与 `replan_count+1`

### Requirement: Replan budget 耗尽确定性终止

系统 SHALL 在 research gap 需要 Replan 但 `replan_count >= max_replans` 时，以 `REQUIRED_EVIDENCE_MISSING` 确定性终止 Run。该分支 MUST 返回 TERMINAL，不得通过普通 IDLE 重复调用 provider。

#### Scenario: ANALYZE gap 耗尽

- **WHEN** ANALYZE 判定 `research_gap` 且 Replan budget 已耗尽
- **THEN** Run 在一次终止接受中进入 FAILED、termination reason 为 `REQUIRED_EVIDENCE_MISSING`，并追加 `RUN_TERMINATED`

#### Scenario: 耗尽后不重复调用模型

- **WHEN** `drive_run` 收到上述终止报告
- **THEN** 驱动立即停止，analyst 和 sufficiency reviewer 不会因同一 fingerprint 再次执行

#### Scenario: Review continuation 时预算已耗尽

- **WHEN** Review Gate response 请求继续研究但接受时 Replan budget 已耗尽
- **THEN** continuation 不创建新 Plan/Task，并以 `REQUIRED_EVIDENCE_MISSING` 终止 Run

### Requirement: provider 失败停止立即重试但不破坏状态

系统 SHALL 将 analyst、Analysis artifact store 或 sufficiency reviewer 的暂时性异常映射为 `UPSTREAM_ERROR` IDLE，并设置 `continue_immediately=False`。失败结果 MUST 不改变 Run、Plan、Task、目标或 accepted Analysis。

#### Scenario: reviewer provider 失败

- **WHEN** `EvidenceSufficiencyReviewer` 抛出异常或返回无效结构
- **THEN** Run 保持 ANALYZING，outcome 为 IDLE `UPSTREAM_ERROR` 且当前 drive 停止

#### Scenario: 后续显式 advance 可重试

- **WHEN** 上游恢复后调用方再次显式 advance 同一 Run
- **THEN** 系统可重新执行 ANALYZE，且仍受既有连续 IDLE attempt budget 约束

### Requirement: gap 数据不得扩大执行权限

系统 SHALL 将 gap hint 视为不可信数据，执行去控制字符、去空白、限长和日志清理，并以固定标签与 objective 分隔。gap 内容 MUST NOT 添加或改变 capability、WorkerRole、allowlist、权限、路由或工具选择。

#### Scenario: 恶意 gap 请求未注册 capability

- **WHEN** gap hint 包含调用未注册 capability、改变 WorkerRole 或绕过权限的文字
- **THEN** FocusedReplanBuilder 忽略这些指令，新 Task 仅使用当前 Plan 已批准且 allowlist 允许的研究 capability

#### Scenario: gap 不作为控制指令

- **WHEN** capability adapter 构造模型或检索请求
- **THEN** gap 内容位于明确标记的不可信数据字段/区块，不被拼接为系统级控制指令

### Requirement: 回环具有结构化关联和效果观测

系统 SHALL 使用既有 Stage、`PLAN_REPLANNED`、Run transition event 与 usage ledger 记录每个 gap 回环。观测 MUST 能关联 gap、Plan 版本、后续通过结果与新增 Evidence，不得仅依赖自由文本 reason。

#### Scenario: 记录 gap 回环

- **WHEN** Focused Replan 成功提交
- **THEN** 结构化记录包含 gap ID、source phase/state version、old/new plan version、focus hash 和新增/保留 Task IDs

#### Scenario: 记录回环后通过

- **WHEN** 某个 gap 对应的新 Plan 后续通过充分性检查
- **THEN** accepted ANALYZE Stage 记录 resolved gap ID、输入 Evidence hash 和该轮新增 Evidence 数量

#### Scenario: 记录额外模型成本

- **WHEN** L1 reviewer 被调用
- **THEN** usage ledger 可归集其 token、cost 与 latency，以计算 gap 命中率、回环通过率、零增量回环数和 sufficient 路径附加成本

### Requirement: 与 REVIEWING 漏斗并存

系统 SHALL 保留 REVIEWING 的既有 verdict 与 Gate 语义。ANALYZE 预判输入为 Analysis + Evidence；现有 REVIEW 输入为 ReportContent。预判通过或记录 conflict 后，REVIEW 仍独立评审报告。

#### Scenario: REVIEWING 仍可判 RESEARCH_GAP

- **WHEN** 报告评审发现预判未捕获的研究缺口
- **THEN** `ReviewPhaseHandler` 仍产生既有 `RESEARCH_GAP` Gate 分支

#### Scenario: REVIEWING 仍可判冲突

- **WHEN** 报告评审发现需人工裁决的冲突
- **THEN** `ReviewPhaseHandler` 保留既有 `CONFLICT` verdict 与 `CONFLICT_RESOLUTION` Gate 行为

#### Scenario: 预判通过不约束报告质量 verdict

- **WHEN** ANALYZE verdict 为 `sufficient` 且报告进入 REVIEWING
- **THEN** REVIEW 可独立返回 PASS、REVISE、RESEARCH_GAP、CONFLICT 或 ESCALATE
