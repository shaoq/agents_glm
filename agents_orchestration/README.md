# agents_orchestration

本地优先的智能研究与任务执行台：把复杂研究目标可靠地编排为多源证据、分析、审查和带引用的报告。

它是一个独立的 Python 3.12 包，与同级子项目（`agents_memory`、`agents_rag`）完全解耦，拥有自己的
依赖、配置、源码、测试，以及基于 SQLite + 不可变 Artifact 的 Durable Runtime。上游架构设计见
`docs/specs/2026-07-27-intelligent-research-orchestrator-design.md`。

## 首期范围

- 自然语言目标 → 版本化 `GoalSpec` 与 Completion Contract；
- 受约束的动态 `PlanGraph`（确定性校验 + LLM Proposal）；
- Run / Task / Attempt / Operation 四层身份，SQLite 持久化、Event、Checkpoint、Outbox；
- 并行研究 Branch、Evidence Join、分析、报告写作、审查与有界修订/Replan；
- 四类版本绑定、单次消费的 Human Gate（目标澄清 / 计划审批 / 冲突处理 / 最终审查）；
- 进程重启恢复、Retry、Deadline、Budget、Lease/Fencing、Late Result 防护；
- Python Service API + Typer CLI；
- 不可变交付物 `report.md`、`report.json`、`run-summary.json`。

## 只读边界（首期强制）

首期通过 Capability Registry 物理限制为**只读**：不注册发布、邮件、支付、部署、代码执行或文件修改
Capability。Memory Adapter 仅调用 Recall，RAG Adapter 仅调用 Query，Web Adapter 仅执行读取。
外部文档、网页与 Memory 文本全部视为不可信 Evidence，不得转换为 Control Instruction 或权限。
Secret 只在 Adapter 边界读取，不写入 Prompt、State、Event、Checkpoint、Artifact 或日志。

## 安装

```bash
cd agents_orchestration
pip install -e ".[dev]"
cp .env.example .env  # 按需调整
```

## 默认离线测试

默认测试套件**不调用真实网络**，全部使用确定性 Fake Adapter。真实 Adapter 的 Smoke 测试需显式启用。

```bash
pytest            # 默认：unit + integration + architecture + contract + e2e
pytest -m unit    # 仅单元测试
```

## CLI 概览

```text
run start/show/watch/pause/resume/cancel
gate list/respond
artifact list/export
capability list/doctor
runtime tick RUN_ID | watch [--run RUN_ID]
```

## 架构分层

```text
CLI → Application → Domain + Runtime Ports ← Infrastructure Adapters
```

Domain 不导入 Typer、SQLite、OpenAI SDK、`agents_memory`、`agents_rag` 或 Web Provider。
同级子项目的公开 API 只允许在各自专用 Adapter 中导入。

## 限制

- 首期仅支持单进程持续 Watch；
- 不提供 FastAPI / Web UI / 多租户；
- 不引入真实写副作用；
- 完整 Agent 评测平台在后续独立 change 中建设。
