## 1. Fake 多源 double（测试替身，下沉 tests/support）

- [x] 1.1 在 `tests/support/` 增加 fake 多源 double（RAG / Memory / Web），各自返回预设的 normalized `Evidence`（`source_kind` 分别为 `RAG` / `MEMORY` / `WEB`，web 的 `is_untrusted=True`）。**不进生产代码**（遵守 `remove-offline-fake-assembly`）。
- [x] 1.2 fake double 接受 `query` 输入（fake web 接 query 返回预设证据，绕过 query→url 转换）
- [x] 1.3 单测：fake double 返回正确的 `source_kind` / `is_untrusted` 标记，且不 import `httpx`/`openai`/`agents_rag`/`agents_memory`

## 2. Planner 多源提议（子问题 + 源标签）

- [x] 2.1 `LLMPlanner` schema 增加 `_ResearchSubQuestion`（`task_id` / `description` / `source_hints: list[Literal["local_knowledge","personal_context","live_web"]]`）
- [x] 2.2 改 prompt：鼓励按目标拆多个研究子问题，并说明 `source_hints` 各标签含义
- [x] 2.3 新增 `_SOURCE_HINT_MAP` 确定性映射（标签 → `(CapabilityKind, BranchRole)`），替代 `_CAPS_FOR_ROLE` 对 research task 的硬编码单源
- [x] 2.4 边界处理：`web_enabled=False` 时丢弃 `live_web` + 诊断；空 `source_hints` fallback `[local_knowledge]`；映射后无源标 `Degradation`
- [x] 2.5 单测：映射正确性、`Literal` 枚举约束、web 过滤、空标签 fallback、无源降级

## 3. Research handler 多源 Branch

- [x] 3.1 新建多源 research handler（替代 `_LLMResearchHandler`）：按 `task.required_capabilities` 构造 `Branch`（角色来自映射表）
- [x] 3.2 handler 内 invoke 适配：用 `registry.find_kind` 把 `capability_kind`→`capability_id`，包成 `dispatch_branches` 期望的 `invoke(kind, request)`
- [x] 3.3 调 `dispatch_branches` 并发 + `EvidenceJoiner.join`（`JoinPolicy`），失败 lane 降级，返回 evidence 落盘
- [x] 3.4 单测：多源并发、invoke 签名适配、OPTIONAL 源失败降级且 task SUCCEEDED、REQUIRED 源失败按策略处理

## 4. Composition 装配与边界收紧

- [x] 4.1 `build_production_coordinator` 暴露 sibling adapter 注入点（`recall_fn`/`query_fn`/`fetch_fn`）；`build_production_coordinator_from_settings` 暂不接真实 sibling（保留注入缝，待后续 change）
- [x] 4.2 装配多源 research handler（`EVIDENCE_RESEARCHER` → 新 handler），移除 `_LLMResearchHandler`
- [x] 4.3 收紧 `allowed_capabilities`：从 `frozenset(CapabilityKind)` 改成实际支持集（`{RAG_SEARCH, MEMORY_RECALL, WEB_RESEARCH?}`）
- [x] 4.4 `WorkerDefinition.allowed_capabilities` 配齐（满足 `Router` 的 allowlist 校验）
- [x] 4.5 集成测试：经 `build_production_coordinator` 注入 fake double，跑通 RESEARCH 多源链路（组件级由 task 6.1 覆盖：handler→router→adapter 端到端）

## 5. Phase 2 并发补全

- [x] 5.1 `runtime/tick.py:170` 把 `for task, attempt in dispatches: await ...` 改为 `asyncio.gather` + `asyncio.Semaphore(run.policy.max_concurrency)`
- [x] 5.2 单测/集成：多 task 并发执行、并发度受 `max_concurrency` 限制、崩溃恢复语义（Lease/Fencing/批量 accept/Budget）不变

## 6. 集成 / E2E 测试

- [x] 6.1 集成：RESEARCH 多源 → EvidenceJoin → ANALYZE 全链路（经 `build_production_coordinator` 注入 fake double）
- [x] 6.2 集成：多源 research task 并发执行验证
- [x] 6.3 集成：`web_enabled=False` 时 web 源被映射过滤、task 不带该源
- [x] 6.4 架构测试：生产 composition 不含任何 Fake 类、不 import 真实 sibling / 网络栈；测试 double 全部位于 `tests/`
- [x] 6.5 覆盖率：新增/改动模块达 80%+

## 7. 文档

- [x] 7.1 更新 `agents_orchestration/README.md`：RESEARCH 多源骨架 + 测试 double 说明 + 真实 Memory/RAG/Web 接线 deferred 备注
- [x] 7.2 live smoke 标记 deferred（与 `add-orchestration-llm-ports` 一致）
