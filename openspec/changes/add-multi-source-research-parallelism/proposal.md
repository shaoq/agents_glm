## Why

`agents_orchestration` 的 RESEARCH 阶段目前是 MVP 单源实现：production 的 research handler（`_LLMResearchHandler`）绕过 `CapabilityRouter`，直接让 LLM 凭参数知识生成 `source_kind=MODEL` 的 evidence——研究证据带有"编造"成色，不是真实检索的产物。`LLMPlanner` 给所有 research task 硬编码 `required_capabilities=(RAG_SEARCH,)`，没有"数据源"概念；`MemoryRecallAdapter`/`RagAdapter`/`WebResearchAdapter` 已实现却未接入 production；`branches.py` 的多源并发 + `EvidenceJoiner` 机制已实现且经过测试，但只在 tests 里跑，未接进 phase handler。同时 `tick.py` Phase 2 是 `for ... await` 串行执行，`max_concurrency` 字段贯穿 `SystemLimits→RunPolicy→Scheduler` 选批却在 execute 处失效，实际并发度 = 1。

这是 `add-orchestration-llm-ports` proposal 明确 defer 的"独立 change"（原文：真实 Memory/RAG 适配器接线缓后到独立 change）。多源真实检索是"研究而非编造"的核心，Task 并行是多源 I/O 的效率前提——两者耦合，放同一个 change。

## What Changes

- **research task 改为按子问题切分（M2）**：`LLMPlanner` 把研究目标拆成语义独立的子问题，每个子问题一个 `EVIDENCE_RESEARCHER` task，query 为子问题级（精准），而非笼统的整目标。
- **LLM 建议语义源标签 + 确定性映射（B）**：Planner schema 增加受约束枚举 `source_hints`（`local_knowledge`/`personal_context`/`live_web`）；LLM 只描述"需要什么特性的知识"，不碰 `CapabilityKind` 枚举；确定性映射表把标签翻译成 `(CapabilityKind, BranchRole)`，经三层卡边界（映射过滤 / `PlanValidator` / `Router` policy）。
- **research handler 改为多源 Branch**：从直连 LLM 的 `_LLMResearchHandler` 改为"按 `task.required_capabilities` 构造 Branch → `dispatch_branches` 并发 → `EvidenceJoiner` Join"，复用 `branches.py`；失败 lane 由 `JoinPolicy` 降级，task 仍 SUCCEEDED，多源容错封装在 handler 内（`ResearchPhaseHandler` 的 Join 逻辑不改）。
- **Phase 2 并发补全**：`tick.py` 的 `for-await` 改为 `asyncio.gather + Semaphore(run.policy.max_concurrency)`，使 `max_concurrency` 名副其实。
- **fake 多源 adapter 接入**：用确定性 fake double 提供 RAG/Memory/Web 预设证据，跑通完整编排链路；**不接真实 sibling service**（`RecallResult`/`Answer` 契约不对齐，deferred 到后续 change）。
- **composition 装配 + 边界收紧**：注册 fake adapter、装配多源 research handler、把 `allowed_capabilities` 从全开（`frozenset(CapabilityKind)`）收紧到实际注册集，让 `PlanValidator` 真正起边界作用。

## Capabilities

### New Capabilities

- `multi-source-research-routing`: 子问题级 research task 切分、LLM 语义源标签建议、确定性 capability 映射与三层边界守卫、handler 内多源 Branch 并发与 `EvidenceJoiner` 容错、以及多源 task 的并行执行。

### Modified Capabilities

<!-- 现有 openspec/specs/ 下的 capability（contextual-retrieval / query-pipeline / hybrid-indexing 等）均属 RAG/Memory 检索能力，本 change 不改变其 spec 级需求。orchestration 侧此前的"六能力"未在 openspec/specs 落盘，故无 Modified 项。 -->

## Impact

- **代码**：`orchestration/llm_ports.py`（`LLMPlanner` schema + prompt + 确定性映射）、`orchestration/composition.py`（装配 + `allowed_capabilities` 收紧）、新增多源 research handler（复用 `branches.py`）、`runtime/tick.py`（Phase 2 并发）、`adapters/fake.py`（fake 多源 evidence）；可能微调 `branches.py` 的 `invoke` 签名适配。
- **不接真实 sibling service**：`agents_rag.QueryPipeline.ask` / `agents_memory.MemoryService.recall` 的契约适配（`RecallRequest`/`RecallResult`/`Answer`）是后续独立 change。
- **既有纪律不变**：只读边界、不可信证据标记（web/model 的 `is_untrusted=True`）、降级披露（`Degradation`）。
- **依赖现有 policy**：`RunPolicy.web_enabled`（默认关）+ `web_allowed_domains` 域名白名单；`SystemLimits.max_concurrency` 默认 4。
