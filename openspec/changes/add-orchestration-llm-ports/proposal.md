## Why

`agents_orchestration` 的协调框架（RunCoordinator / phase handlers / Task Runtime / 持久化）已就绪且经过
完整测试，但五个模型驱动阶段端口（`GoalNormalizer` / `Planner` / `Analyst` / `ReportWriter` /
`ReportReviewer`）目前全部是 `orchestration/composition.py` 里的确定性 Fake 占位
（`_FakeGoalNormalizer` / `_FakePlanner` / `_FakeAnalyst` / `_FakeWriter` / `_FakeReviewer`）。
默认 `run start` 走 `build_offline_coordinator`，产出的 `report.md` 是空壳内容，不是真实研究报告。

本 change 把这五个端口接到真实 LLM（OpenAI 兼容的 `glm-5.2`，经智谱 `paas/v4`），让
`agents-orchestration run start --goal "…"` 真的产出有内容、带引用、可审查的研究报告。

已验证（探针实测 2026-07-28）：`glm-5.2` 经 `paas/v4` 同时支持基线 chat、
`response_format={"type":"json_object"}` 与 `tools`（function calling，`finish_reason=tool_calls`，
`tool_call.function.arguments` 为合法 JSON）。本 change 采用 **function calling** —— schema 强约束、
输出即合法结构，把「LLM 输出不可靠」这一最大风险基本消除。

## What Changes

- 新增 5 个 LLM-backed phase port 实现：`LLMGoalNormalizer` / `LLMPlanner` / `LLMAnalyst` /
  `LLMReportWriter` / `LLMReportReviewer`。每个端口用 function calling 把对应领域输出模型
  （`GoalNormalizationOutcome` / `PlanProposal` / `AnalysisArtifact` / `ReportContent` /
  `ReviewProposal`）的 JSON Schema 作为 tool definition 传给模型，解析 `tool_call.function.arguments`
  → Pydantic 校验；解析失败或 provider 异常时降级（`IDLE` + 诊断），绝不伪造成功。
- Research 阶段引入 **LLM 知识源 provider（R1）**：`EvidenceResearcher` 任务由 LLM 基于其内部知识
  产出 `Evidence`（`source_kind=MODEL`、`is_untrusted=True`），让全链路真实跑通。真实 Memory / RAG
  适配器接线**缓后**到独立 change，本 change 保留其 Fake 占位、不接真实兄弟服务。
- 补 **evidence 持久化缺口**：Task Runtime `accept` 阶段目前只存 artifact 引用，未持久化
  `CapabilityResult.evidence`，导致 `evidence_provider(run_id)` 无从读取。本 change 在 accept 时
  持久化已接受证据，`evidence_provider` 从持久化读取供 Research Join / Analysis 使用。
- 扩展 `OpenAIModelAdapter` 支持 `tools`（function calling）调用模式，供 5 个端口与 Research
  provider 复用；保留纯文本模式用于报告正文生成。
- 新增 **production composition root**：装配真实 LLM registry（`OpenAIModelAdapter` 注册为 MODEL
  capability）+ 5 个 LLM 端口 + Research LLM-provider + evidence_provider；Memory/RAG/Web adapter
  保留 Fake 占位。CLI 默认走 production（`run start`）；离线 composition 仅供测试。
- 离线单元测试（mock LLM 返回，验证 schema 解析 / 降级 / evidence 持久化）+ 可选 live smoke
  测试（真实 `glm-5.2`，`@pytest.mark.smoke` 默认跳过）。

## Capabilities

### New Capabilities

- `llm-phase-execution`: model-backed phase ports emit structured Proposals/artifacts via function
  calling, degrade explicitly on failure, and persist accepted evidence so downstream phases
  (Analysis / Write / Review) consume real research output.

### Modified Capabilities

- `orchestration-control-surface`: production composition wires real LLM ports; `run start` defaults
  to production (create-and-drive) with offline composition reserved for tests.
