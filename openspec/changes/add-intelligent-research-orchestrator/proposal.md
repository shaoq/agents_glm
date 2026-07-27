## Why

`agents_orchestration` 已形成系统性的编排知识，但尚无应用实现来验证动态任务图、持久恢复、
能力路由、人工 Gate 和有界终止能否作为一个完整系统协作。现在需要建设一个本地优先的智能
研究与任务执行台，把复杂研究目标可靠地编排为多源证据、分析、审查和带引用报告，同时复用
现有 Memory 与 RAG 的公开边界。

## What Changes

- 新增独立 Python 包 `agents_orchestration`，提供 Python Service API 与 Typer CLI。
- 新增 SQLite Durable Runtime，持久化 Run、Task、Attempt、Plan、Event、Checkpoint、Lease、
  Gate、Capability Call 和 Artifact Metadata。
- 新增受约束的动态研究规划：LLM 生成 Goal、Plan 和 Replan Proposal，确定性组件负责校验和提交。
- 新增 Worker Definition、Capability Registry、Capability Router 和可替换 Adapter，首期接入
  Memory Recall、RAG Knowledge Search、可选 Web Research 和 Model。
- 新增并行研究、Evidence Join、分析、报告写作、审查和有界修订/重规划闭环。
- 新增四类可配置 Human Gate：目标澄清、计划审批、冲突处理和最终审查。
- 新增进程重启恢复、Retry、Deadline、Budget、Lease/Fencing、Late Result 拒绝和结构化降级。
- 新增 `report.md`、`report.json` 和 `run-summary.json` 三类最终交付物。
- 首期通过 Capability Registry 强制保持只读，不提供发布、邮件、支付、部署、代码执行或文件修改。

## Capabilities

### New Capabilities

- `durable-orchestration-runtime`: Run、Task、Attempt 状态机，SQLite 持久化、Event、Checkpoint、
  Lease、Retry、恢复、预算和终止语义。
- `dynamic-research-planning`: GoalSpec、Completion Contract、受约束 PlanGraph、确定性计划校验与
  有界 Replan。
- `research-capability-routing`: Worker/Capability 注册、权限与策略路由，可切换的 Memory、RAG、
  Web 和 Model Adapter，并行研究与 Evidence Join。
- `human-gated-orchestration`: 版本绑定、单次消费的目标澄清、计划审批、冲突处理和最终审查 Gate。
- `research-report-delivery`: Evidence 分析、报告写作、审查、完成验证及 Markdown/JSON/Run Summary
  Artifact 交付。
- `orchestration-control-surface`: Python OrchestrationService、CLI Run/Gate/Artifact/Capability/Runtime
  命令和结构化诊断。

### Modified Capabilities

无。现有 OpenSpec capability 的需求不发生变化。

## Impact

- 新增 `agents_orchestration/pyproject.toml`、`src/agents_orchestration/`、测试、配置、CLI、
  SQLite Storage 和 Artifact 目录。
- `agents_memory` 与 `agents_rag` 不修改、不反向依赖 Orchestrator；仅由 Orchestrator Adapter
  调用其公开 Python API。
- 新增 Python 3.12 依赖，预计包括 Pydantic、pydantic-settings、Typer、Rich、pytest、
  pytest-asyncio、pytest-cov 和 Ruff；外部模型继续通过 OpenAI-compatible Adapter 接入。
- 默认测试使用 Fake Adapter，不访问真实网络；真实 API 仅用于显式启用的 Smoke Test。
- 首期是本地单进程模块化单体，不引入 FastAPI、Web UI、消息队列、分布式 Worker 或第三方
  Workflow Engine 作为领域核心。
