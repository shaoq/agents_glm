## Context

`agents_orchestration` 的确定性协调框架（RunCoordinator / phase handlers / RuntimeTick / SQLite 持久化 /
Gate / 状态机）已在 `add-intelligent-research-orchestrator` 与 `add-orchestration-run-coordinator` 中
完成并测试。但五个模型驱动阶段端口是 `orchestration/composition.py` 里的 Fake 占位，`run start` 跑不出
真实研究报告。

本 change 只补「智能体肉身」（LLM 端口 + Research 知识源 + evidence 流 + production 装配），**不动框架
不变量**（coordinator / phase handler 协议 / tick / 状态机 / CAS / lease 保持原样）。

主要约束：

- Python 3.12、共用 conda 环境 `agents_glm`、OpenAI 兼容的 `glm-5.2`（智谱 `paas/v4`）。
- Port 仍只发 Proposal / 产 artifact；正式状态只由确定性 `accept` 提交（设计 Decision 2 不变）。
- Memory / RAG / Web 真实适配器**缓后**到独立 change；本 change 保留其 Fake 占位。
- 默认离线测试不触网；live smoke 显式启用。
- LLM 输出不可靠是最核心风险 → 用 function calling 的 schema 强约束消除。

实测证据（2026-07-28 探针，`glm-5.2` + `paas/v4`）：

| 模式 | 结果 |
|---|---|
| 基线 chat | 通过，模型即使不指定格式也常输出 JSON |
| `response_format={"type":"json_object"}` | 通过，`json.loads` 成功 |
| `tools`（function calling） | 通过，`finish_reason=tool_calls`，`tool_call.function.arguments` 合法 JSON |

## Goals / Non-Goals

**Goals:**

- 5 个 phase port 真实化（function calling → 结构化领域对象）；
- Research 阶段有真实证据流入（LLM 知识源，R1）；
- 已接受证据可被下游阶段读取（补 evidence 持久化缺口）；
- production composition 让 `run start` 默认产出真实研究报告；
- 失败显式降级，不伪造成功（设计 Decision 13）。

**Non-Goals:**

- 接线真实 `agents_memory.MemoryService` / `agents_rag.QueryPipeline`（独立 change）；
- Web Research 真实适配（独立 change，默认禁用不变）；
- 模型型 Worker 的精细 prompt 调优 / 评测（本 change 只保证结构正确 + 可跑通）；
- 多模型路由 / A-B 测试 / 质量评测平台。

## Decisions

### 1. 采用 function calling（而非 prompt+parse JSON）

每个 LLM 端口把**输出领域模型的 JSON Schema** 作为单个 tool 的 `parameters` 传给模型：

```text
LLM 端口
  → OpenAIModelAdapter.invoke_tools(prompt, tool_schema)
  → glm-5.2 返回 tool_call.function.arguments（合法 JSON）
  → Pydantic 模型校验 → 领域对象（GoalNormalizationOutcome / PlanProposal / ...）
```

**理由**：schema 强约束，模型直接产结构化 args，无需「去 markdown fence / 截 {…} / 重试」的脆弱
解析；探针实测 `glm-5.2` 的 function calling 稳定返回合法 JSON。降级路径仍保留（校验失败 →
`IDLE` + 诊断，phase handler 已支持 task 5.2/7.2 的 provider 失败降级）。

**替代方案**：`response_format=json_object` + prompt 描述 schema。可行（探针通过），但需自行解析 +
容错，可靠性低于 function calling，故不取。

### 2. Research 用 LLM 知识源（R1）

Research 阶段不接真实 Memory/RAG，改为：`EvidenceResearcher` 任务由 LLM 基于内部知识产出
`Evidence`（`source_kind=MODEL`、`is_untrusted=True`、`source_id=model:glm-5.2`）。全链路（Goal→Plan
→Research→Analyze→Write→Review→Finalize）真实跑通，报告有真实内容；证据**来源**待后续从
LLM-知识 换成 Memory/RAG-检索（换 Research provider 即可，不动框架）。

**理由**：让核心链路立即产出可用的真实报告；Memory/RAG 适配涉及重写映射层（`MemoryService.recall`
收 `RecallRequest`、返回 `EvidenceGroup`；`QueryPipeline.ask` 返回 `Answer.citations`），工作量大且
依赖兄弟项目数据，缓后到独立 change 更清晰。

### 3. evidence 持久化：accept 时写 evidence 表（C1）

当前 `RuntimeTick._accept_success` 只存 artifact 引用，未持久化 `CapabilityResult.evidence`。新增
`evidence` 持久化：accept 阶段把已接受证据写入 store，`evidence_provider(run_id)` 从该 store 读取，
供 `ResearchPhaseHandler`（Join）与 `AnalysisPhaseHandler` 使用。

**理由**：可查询、可去重、与 `EvidenceSet.join` / 冲突检测自然衔接；比序列化进 artifact 更适合后续
接真实 Memory/RAG（它们的证据也走同一持久化路径）。

### 4. OpenAIModelAdapter 扩展 tools 模式

新增 `invoke_tools(request, tools)`：在现有 `invoke`（纯文本）之上，支持传 `tools` 参数，返回
`CapabilityResult.data = {"tool_call": {name, arguments}, ...}`。secret 仍只在边界读取、不入 prompt/
state/log（Decision 12 不变）。纯文本 `invoke` 保留（ReportWriter 正文生成用）。

### 5. production composition：fail-loudly，Fake 仅测试

`build_production_coordinator` 装配真实 LLM 端口 + Research LLM-provider + evidence_provider；缺端口
`CompositionError` 大声失败（task 9.8 不变）。`Memory`/`Rag`/`Web` adapter 保留 Fake 占位（标
`TODO: 独立 change 接真实兄弟服务`）。CLI `run start` 默认走 production；`build_offline_coordinator`
仅供测试。

### 6. secret 与配置

`OpenAIModelAdapter` 从 `Settings`（`ORCH_LLM_API_KEY` / `ORCH_LLM_BASE_URL` / model profiles）拿
`ModelProfile`；secret 只在 adapter 边界。production composition 不依赖兄弟项目的模块级 settings 单例
（避免 CWD/.env 路径坑）。

## Risks / Trade-offs

- **[LLM 输出偶发不合规]** → function calling schema 强约束 + Pydantic 校验 + 失败降级 `IDLE`
  （phase handler 已支持），不崩溃 Run；BudgetGuard 兜底无限重试。
- **[Research 证据是 LLM 自产]** → 全部标 `is_untrusted=True`、`source_kind=MODEL`，报告 run-summary
  披露「证据来源为模型知识，非外部检索」；接真实 Memory/RAG 后来源切换、框架不变。
- **[token 成本]** → 一次完整 run 多次调 glm；BudgetGuard（token/cost/deadline）兜底；live smoke
  默认跳过。
- **[evidence 持久化改 tick accept]** → 新增 evidence 写入与现有 artifact/event/checkout 原子事务
  内提交，不破坏 CAS / 原子性。
- **[Memory/RAG 缓后]** → 本 change 不接真实兄弟服务；Research 用 LLM 知识源顶替，诚实标注。

## Migration Plan

全新功能（端口实现 + evidence 持久化 + composition），不改既有领域模型/状态机/持久化 schema 的语义
（仅新增 `evidence` 持久化）。交付顺序：

1. `OpenAIModelAdapter.invoke_tools`（function calling 支持）+ 离线测试；
2. evidence 持久化（accept 写 + provider 读）+ 集成测试；
3. 5 个 LLM 端口（Goal → Plan → Analysis → Write → Review）+ Research LLM-provider；
4. production composition + CLI 默认走 production；
5. live smoke（真实 glm-5.2）端到端验证。

Rollback：端口实现位于新文件 / composition 新增 `build_production_coordinator`；切回
`build_offline_coordinator` 即恢复 Fake 行为，框架与既有测试不受影响。

## Open Questions

- ReportWriter 正文较长（markdown），function calling 的 `arguments` 可能不适合放长文本 → Writer
  可能用纯文本 `invoke`（prompt 要求带 `[N]` 引用）而非 function calling；实施时按产出大小决定
  （见 tasks 3.4）。
- evidence store 是新表还是复用 artifact？倾向新表（可查询、去重），但实施时若 artifact 方案更简可
  评估（见 tasks 2.1）。
