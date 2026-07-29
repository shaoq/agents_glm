## ADDED Requirements

### Requirement: 子问题级 research task 切分

`LLMPlanner` SHALL 把研究目标拆成语义独立的子问题，每个子问题对应一个 `EVIDENCE_RESEARCHER` task，且该 task 的检索 query 为子问题级语义描述，而非笼统的整目标。

#### Scenario: 多子问题目标

- **WHEN** `LLMPlanner` 收到目标"总结 AI Agent 技术演进"
- **THEN** 它提议多个 research task（如趋势 / 代表产品 / 观点分歧），每个 `task.description` 是一个独立子问题

#### Scenario: 单子问题目标

- **WHEN** 目标本身单一、无需拆分
- **THEN** Planner 提议单个 research task，其 description 为该目标

### Requirement: LLM 语义源标签建议

`LLMPlanner` SHALL 为每个 research 子问题输出受约束的 `source_hints` 标签（取值仅限 `local_knowledge` / `personal_context` / `live_web`）。LLM MUST NOT 直接输出 `CapabilityKind` 枚举值。

#### Scenario: 受约束枚举

- **WHEN** LLM 输出 `source_hints`
- **THEN** 每个值只能是 `local_knowledge` / `personal_context` / `live_web`（`Literal` 枚举）；非法值触发解析失败并降级（`PortError` → `IDLE`）

#### Scenario: LLM 不碰 capability 枚举

- **WHEN** `source_hints` 被消费
- **THEN** 标签到 capability 的映射由确定性代码完成；LLM 的输出文本中不含 `CapabilityKind` 枚举名

### Requirement: 确定性 capability 映射与三层边界守卫

系统 SHALL 把 `source_hints` 确定性映射成 `(CapabilityKind, BranchRole)`（`local_knowledge`→`(RAG_SEARCH, REQUIRED)`、`personal_context`→`(MEMORY_RECALL, REQUIRED)`、`live_web`→`(WEB_RESEARCH, OPTIONAL)`），并经三层卡边界：映射过滤、`PlanValidator.allowed_capabilities`、`Router` policy。

#### Scenario: 正常映射

- **WHEN** 某子问题 `source_hints = [local_knowledge, live_web]`
- **THEN** 映射为 `required_capabilities=(RAG_SEARCH, WEB_RESEARCH)`、`branch_roles=(REQUIRED, OPTIONAL)`

#### Scenario: web 被策略禁用

- **WHEN** `source_hints` 含 `live_web` 但 `RunPolicy.web_enabled = False`
- **THEN** 映射时丢弃 `WEB_RESEARCH` 并记诊断；该 task 不携带注定失败的源

#### Scenario: capability 未注册或未允许

- **WHEN** 映射后的 capability 不在 `allowed_capabilities`
- **THEN** `PlanValidator` 拒绝该 plan，并在 diagnostics 标注 unsupported capability

#### Scenario: 空标签 fallback

- **WHEN** 某子问题 `source_hints` 为空
- **THEN** fallback 到默认 `[local_knowledge]`，task 至少能查一个源

#### Scenario: 映射后无可用源

- **WHEN** 所有标签被过滤或未知，映射后无 capability
- **THEN** task 走 no-capability 分支：返回空 evidence + `Degradation` 披露，不崩溃 Run

### Requirement: handler 内多源 Branch 并发与 EvidenceJoin

research handler SHALL 按 `task.required_capabilities` 构造 `Branch`（角色来自映射表），经 `dispatch_branches` 并发调用各源 adapter，再用 `EvidenceJoiner` 汇总成 evidence。失败 lane 由 `JoinPolicy` 处理。

#### Scenario: 多源并发检索

- **WHEN** 一个子问题 task 带 `(RAG_SEARCH, WEB_RESEARCH)`
- **THEN** handler 并发调用 RAG 与 Web adapter，汇总其 evidence

#### Scenario: invoke 签名适配

- **WHEN** handler 调用 `dispatch_branches`
- **THEN** 用 `registry.find_kind` 把 `capability_kind` 转成 `capability_id`，经 `CapabilityRouter` 路由到对应 adapter

### Requirement: lane 级容错（OPTIONAL 源失败降级）

当 OPTIONAL 数据源失败时，系统 SHALL 经 `JoinPolicy` 降级（`Degradation` 披露），task 仍 `SUCCEEDED`，且不阻塞 `ResearchPhaseHandler` 的跨子问题 Join。

#### Scenario: OPTIONAL 源失败不阻塞

- **WHEN** task 的 OPTIONAL `web` 源失败但 REQUIRED `rag` 源成功
- **THEN** `EvidenceJoiner` 产出带 `Degradation` 的 evidence，task 仍 `SUCCEEDED`

#### Scenario: REQUIRED 源失败按策略处理

- **WHEN** task 的 REQUIRED 源失败
- **THEN** 按 `JoinPolicy.on_required_fail` 处理（`FAIL` 或 `DEGRADE`），并披露降级

### Requirement: 多源 task 并行执行

`RuntimeTick` Phase 2 SHALL 用 `asyncio.gather` + `asyncio.Semaphore(max_concurrency)` 并发执行一批 dispatch，并发度受 `RunPolicy.max_concurrency` 限制，且不破坏 Durable Runtime 的可靠性保证（Lease/Fencing/批量 accept/Recovery）。

#### Scenario: 批量并发执行

- **WHEN** 一个 tick 选中多个 ready research task
- **THEN** 它们并发执行（受 Semaphore 限），而非串行 `for-await`

#### Scenario: 并发度受控

- **WHEN** `max_concurrency = 2` 且有 4 个 ready task
- **THEN** 同时至多 2 个 task 在执行

#### Scenario: 并发崩溃可恢复

- **WHEN** 并发执行中进程崩溃
- **THEN** 未完成 task 仍处 `DISPATCHED`，`RecoveryManager` 过期 lease 并重排；accept / fencing / budget 语义与串行时一致

### Requirement: fake 多源 adapter 接入

系统 SHALL 用确定性 fake double 提供 RAG / Memory / Web 预设 evidence，跑通"Planner 多源提议 → handler 多源 Branch → EvidenceJoiner Join → 跨子问题汇总"完整链路；production composition MUST NOT 调用真实 `agents_rag` / `agents_memory` service。

#### Scenario: fake 多源 evidence

- **WHEN** fake RAG / Memory / Web adapter 被调用
- **THEN** 返回各自预设的 normalized `Evidence`（`source_kind` 分别为 `RAG` / `MEMORY` / `WEB`，web/model 的 `is_untrusted=True`）

#### Scenario: 不接真实 sibling service

- **WHEN** production composition 装配 research handler
- **THEN** 使用 fake adapter；不导入或调用 `agents_rag.QueryPipeline` / `agents_memory.MemoryService` 真实实现
