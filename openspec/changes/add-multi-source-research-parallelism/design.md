## Context

当前 RESEARCH 阶段是 `add-orchestration-llm-ports` 引入的 MVP 单源实现：`_LLMResearchHandler` 绕过 `CapabilityRouter`，直接让 LLM 凭参数知识生成 `source_kind=MODEL` 的 evidence。研究证据带有"编造"成色，不是真实检索的产物。

`LLMPlanner` 给所有 research task 硬编码 `required_capabilities=(RAG_SEARCH,)`（`llm_ports.py:55`），无"数据源"概念。`MemoryRecallAdapter`/`RagAdapter`/`WebResearchAdapter` 已实现却未接入 production composition；`branches.py` 的 `dispatch_branches`/`EvidenceJoiner`/`JoinPolicy` 已实现且有测试，但只在 tests 里跑。`tick.py:170` Phase 2 是 `for ... await` 串行执行，`max_concurrency` 字段贯穿 `SystemLimits→RunPolicy→Scheduler` 选批却在 execute 处失效。

本 change 是 `add-orchestration-llm-ports` proposal 明确 defer 的"独立 change"。目标：把多源检索（fake 接入）+ 并行执行接入 RESEARCH，让研究证据来自多源检索而非 LLM 凭记忆。

约束：adapter 工厂（`recall_fn_from_memory_service`/`query_fn_from_rag_pipeline`）与真实 sibling service 契约不对齐（`recall(RecallRequest)→RecallResult`、`ask(query)→Answer`），故本 change 用 fake double，真实接线 deferred。

## Goals / Non-Goals

**Goals:**

- research task 按**子问题维度**切分（M2），query 为子问题级。
- LLM 建议**语义源标签** + 确定性映射守边界（B）。
- research handler 改为**多源 Branch**（复用 `branches.py` 的 `dispatch_branches`/`EvidenceJoiner`）。
- Phase 2 **真并发**（`asyncio.gather + Semaphore(max_concurrency)`）。
- fake 多源 adapter 跑通完整编排链路。

**Non-Goals:**

- 不接真实 `agents_rag`/`agents_memory` service（契约适配 deferred）。
- 不改 `ResearchPhaseHandler` 的 Join 判定逻辑（多源容错在 handler 内消化，对上层透明）。
- 不实现 web 的 query→url 真实转换（fake 阶段简化）。
- 不引入子问题间依赖（首期子问题独立并行）。
- 不做完整 Agent 评测平台 / 多 Watch 进程。

## Decisions

### 决策 1：task 切子问题维度（M2），不切数据源

LLM Planner 拆研究子问题，每个子问题一个 `EVIDENCE_RESEARCHER` task。

**Rationale**：query 精准（研究质量命门）；LLM 拆子问题（语义）/ 确定性守源（边界）各司其职；多源容错封装在 handler 内（`ResearchPhaseHandler` 的 "all SUCCEEDED 才 Join" 不用改——OPTIONAL 源失败在 handler 内降级，不冒泡成 task 失败）；task 级可靠性（Attempt/Lease/Retry）+ lane 级容错（JoinPolicy）两层各司其职。

**Alternatives**：
- M1（按数据源切 task）：每源 task 的 query 笼统（一查全），召回质量差。
- M3（子问题×数据源笛卡尔积）：task 膨胀（易撞 `max_tasks=32`），且需在 PlanGraph 引入"子问题分组"语义。

### 决策 2：LLM 建议语义源标签（B）+ 确定性映射

Planner schema 增加受约束 `Literal` 枚举 `source_hints`（`local_knowledge`/`personal_context`/`live_web`）。LLM 只描述"需要什么特性的知识"，不碰 `CapabilityKind` 枚举。确定性映射表：

```
local_knowledge  → (RAG_SEARCH,    REQUIRED)
personal_context → (MEMORY_RECALL, REQUIRED)
live_web         → (WEB_RESEARCH,  OPTIONAL)
```

三层卡边界：映射过滤（`web_enabled=False` 时静默丢弃 `live_web` + 诊断）→ `PlanValidator`（`cap ∈ allowed_capabilities`，`planner.py:61`）→ `Router` policy（`web_enabled` + 域名白名单，`router.py:47`）。

**Rationale**：不同子问题查不同源（研究质量）；LLM 只产语义词，即使映射逻辑被绕过也拼不出越权 capability；扩展源时 LLM 标签体系不变。

**Alternatives**：
- A（全局默认策略）：所有子问题查同源，实现最简但质量折损（时效性问题没查 web）。
- C（按子问题类型规则）：需维护 type 体系，介于 A/B。
- 标签用直接源名（`rag`/`memory`/`web`）：更简单但 LLM 实质碰了 capability 选择，边界感弱。

### 决策 3：handler 内多源 Branch（复用 branches.py）

research handler 从直连 LLM 的 `_LLMResearchHandler` 改为：按 `task.required_capabilities` 构造 `Branch`（角色来自映射表）→ `dispatch_branches` 并发 → `EvidenceJoiner.join`（带 `JoinPolicy`）。失败 lane 降级，task 仍 SUCCEEDED。

**适配点**：`WorkerExecutor` 给 handler 的 `invoke(CapabilityRequest)`（按 `capability_id` 路由）与 `dispatch_branches` 期望的 `invoke(capability_kind, request)`（按 kind）签名不同；handler 内用 `registry.find_kind` 做 kind→capability_id 转换（`service.py:65` 的 `DefaultWorkerHandler` 已有此模式）。

**Rationale**：`branches.py` 的整套机制（`dispatch_branches`/`EvidenceJoiner`/`JoinPolicy`）正是为"单 task 内多源并发 + 容错"设计，当前闲置；接入即成为执行主干，是组装非新造。

### 决策 4：Phase 2 gather + Semaphore(max_concurrency)

`tick.py:170` 的 `for task, attempt in dispatches: await executor.execute(...)` 改为 `asyncio.gather` + `asyncio.Semaphore(run.policy.max_concurrency)`。

**可靠性核对**（不破坏 Durable Runtime）：
- 执行在事务外（不持写锁）。
- 每 task 独立 Lease（Phase 1 已发）。
- Phase 3 批量 accept 已就绪（`_accept` 的 `for outcomes` 循环）。
- Budget 在 Phase 3 统一消费。
- Recovery 语义不变（崩溃后 task 仍 DISPATCHED，`RecoveryManager` 重排）。

### 决策 5：fake adapter 接入（不接真实 sibling）

边界适配用确定性 fake double，提供 RAG/Memory/Web 预设 evidence。

**Rationale**：真实 sibling 契约不对齐（`RecallResult`/`Answer`），适配是独立工作；先用 fake 跑通"Planner 多源提议 → handler 多源 Branch → EvidenceJoiner Join → 跨子问题汇总"完整链路，验证 M2+B 设计正确。真实 Memory/RAG/Web 接线是后续 change。

## Risks / Trade-offs

- **[handler 内并发不受 tick `max_concurrency` 管控]** → handler 内自限（单 task 源数少，2-3）；跨 task 并发仍由 tick 的 Semaphore 管控。
- **[LLM 选源质量不可控（选错/选漏/未知标签）]** → `Literal` 枚举强约束 + validator 卡 allowed + fallback（空 `hints`→默认 `local_knowledge`）+ 降级披露；映射后无源走 no-capability 分支。
- **[web 标签被选但 `web_enabled=False`]** → 映射时静默丢弃 + 记诊断，task 不带注定失败的源。
- **[fake 不代表真实]** → 真实 sibling 适配是后续 change；fake 只验证编排正确性，不验证检索质量。
- **[handler 重写工作量大]** → 复用 `branches.py` 现成机制，是组装非新造。

## Migration Plan

本地开发、无远端，无在线迁移。`composition.py` 切换装配（fake adapter + 多源 research handler + 收紧 `allowed_capabilities`）。旧的 `_LLMResearchHandler` 可保留对照或移除。灰度路径：先单源 fake 跑通，再加多源 + 并发。

## Open Questions

- **web lane 的 query→url 转换**：fake 阶段 fake web adapter 接 query 返回预设证据绕过；真实接入时需 LLM 或搜索引擎把子问题转成搜索 url（后续 change）。
- **子问题间依赖**：首期独立并行；若出现"观点分歧依赖先了解趋势"，后续用 `PlanGraph.dependencies` 表达。
