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

## 执行模型（RunCoordinator）

公开入口 `start_and_drive(raw_goal, request_id)` 创建 CREATED Run 并驱动 `RunCoordinator`
走完固定 7 阶段生命周期，直到 terminal 或显式 block：

```
CLI / Python API → OrchestrationService → RunCoordinator.advance
  ├── CREATED → NORMALIZING（Goal：normalize + Completion Contract）
  ├── PLANNING（Plan：propose + 确定性 validate + accept，可选 PLAN_APPROVAL Gate）
  ├── RESEARCHING（EVIDENCE_RESEARCHER Task + Evidence Join → EvidenceSet）
  ├── ANALYZING（ANALYST Task → AnalysisArtifact）
  ├── WRITING（REPORT_WRITER Task → Report Draft）
  ├── REVIEWING（REPORT_REVIEWER → PASS/REVISE/RESEARCH_GAP/CONFLICT/ESCALATE，有界 revision/Replan）
  └── FINALIZING（CompletionEvaluator + ReportBuilder + Finalizer → terminal + report artifacts）
```

- 每次 `advance` 至多推进一个语义 phase，返回 `AdvanceReport`（PROGRESSED / BLOCKED / IDLE / TERMINAL）；
- model / evidence 内容只能发 Proposal，正式 state 转换由确定性组件决定；
- Gate 携带 version-bound continuation，consume 时确定性恢复下一 phase（caller 无法任意指定 target）；
- `--create-only` 只持久化 CREATED Run；`runtime tick RUN_ID` 为单次 advance；`runtime watch` 循环 advance；
- `TaskRuntimeTick` 仍是 Task attempt 执行单元，按当前 phase 过滤 eligible Worker role。
- **task 重试真正生效**：retryable 失败的 task 进入 `AWAITING_RETRY` 后，下一次 Tick 在 backoff 到期时
  自动转回 `READY` 重新派发（指数退避 `base*2^(n-1)`，封顶 60s，确定性基于 clock），重试预算用尽
  （`max_attempts_per_task`）才 `FAILED`。backoff 未到期属于 WAITING：不消耗 phase IDLE 预算，
  `drive_run` 返回并让出控制，等待下一次外部 tick/watch；Attempt 接纳时同步释放匹配 Lease，
  避免重试 epoch 重叠。
- **phase IDLE 有界放弃**：只统计当前 fingerprint 下连续、真正消耗预算的 IDLE；历史 fingerprint、
  BLOCKED、stale 与 WAITING observation 都会中断计数。缺失 fingerprint 时 Coordinator 会根据当前
  Run/Plan 版本确定性补全。连续次数达到 `max_attempts_per_task` 时，Run 有界终止为 `FAILED`
  （`ATTEMPTS_EXHAUSTED`，披露降级），而非让 `drive_run` 空转到 `max_advances`。

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

默认测试套件**不调用真实网络**，全部经 `tests/support` 的确定性 test double 驱动：真实
`Memory/RAG/Web` adapter 注入确定性 `recall_fn`/`query_fn`/`fetch_fn`，阶段端口经
`build_production_coordinator` 的显式注入缝替换为确定性 double（架构测试保证这些 double 不
import httpx/openai/requests）。**生产代码不再包含任何 Fake 或模拟装配**。真实 Adapter 的 Smoke
测试需显式启用（`ORCH_LIVE_SMOKE=1`）。

> **开发模式注意**：生产代码只有一条装配——`OrchestrationService(backend)` 默认经
> `build_production_coordinator_from_settings` 接真实 LLM，因此**本地驱动任意 Run 必须配置
> `.env`（`ORCH_LLM_API_KEY` 等）**，否则会在首个 LLM 端口调用处失败。测试用
> `tests.support.service_factory.build_test_service(backend)` 注入确定性 coordinator，无需网络。

```bash
pytest                       # 全量（unit + integration + architecture + contract + e2e）
pytest -m unit               # 仅单元
pytest -m e2e                # 仅端到端
pytest --cov=agents_orchestration --cov-report=term-missing   # 覆盖率（首期阈值 80%）
```

## CLI

```bash
agents-orchestration --version

# Run 生命周期
agents-orchestration run start --goal "总结 X 并产出报告" --request-id req-1 [--create-only] [--follow]
agents-orchestration run show RUN_ID
agents-orchestration run watch RUN_ID
agents-orchestration run pause RUN_ID --expected-version N
agents-orchestration run resume RUN_ID --expected-version N --target researching
agents-orchestration run cancel RUN_ID --expected-version N

# Human Gate（响应 payload 为类型化契约，见“Gate 工作流”）
agents-orchestration gate list RUN_ID
agents-orchestration gate respond GATE_ID --request-id rq --actor user --role approver \
    --payload '{"outcome":"approved"}'

# Artifact（内容寻址、hash 校验）
agents-orchestration artifact list
agents-orchestration artifact export ARTIFACT_ID

# Capability（secret-safe 诊断）
agents-orchestration capability list
agents-orchestration capability doctor

# Durable Runtime（运维）
agents-orchestration runtime tick RUN_ID          # 必须指定 Run；不改 Pause/Gate/Cancel 状态
agents-orchestration runtime watch --run RUN_ID   # 持续推进单个 Run
agents-orchestration runtime watch                # 推进所有可恢复 Run
```

CLI 只做参数适配与展示，全部领域逻辑在 Application Service。失败映射为稳定退出码：
`404` 未找到、`409` 版本冲突 / 重复请求，其余 `1`，并输出 JSON 诊断。

## Python API

```python
from agents_orchestration.application.service import OrchestrationService
from agents_orchestration.runtime.persistence.connection import SqliteBackend

backend = SqliteBackend("storage/runtime.sqlite", "storage/artifacts")
service = OrchestrationService(backend)

run = service.start_run("研究 X", request_id="req-1")     # 幂等（Request ID 去重）
await service.drive_run(run.run_id)                        # 持续推进直到终态或阻塞
service.pause_run(run.run_id, expected_version=run.state_version)
service.respond_gate(gate_id, request_id="rq", actor="user", role="approver", payload={"outcome": "approved"})
exported = service.export_artifact(artifact_id)            # hash 校验后返回字节
```

## Runtime 命令语义

- `run start` 默认创建并持续推进 Run；`--create-only` 只创建不驱动；`--follow` 阻塞驱动到阻塞/终态。
- `runtime tick RUN_ID` 执行**一次有界 Tick**（必须指定 Run），是可独立恢复/测试的执行单元。
- `runtime watch` 是单进程持续循环（首期只支持一个 Watch 进程）。
- Runtime 命令**不**改动 Pause/Gate/Cancel 状态——这些由 Run 命令或 Gate 流程显式控制，Tick 仅观察。

## Gate 工作流

四类 Gate（`GOAL_CLARIFICATION` / `PLAN_APPROVAL` / `CONFLICT_RESOLUTION` / `FINAL_REVIEW`）绑定
`state_version` / `plan_version` / `artifact_hash`，响应经 Request ID 去重、**单次消费**；到期触发
配置动作（cancel/fail/partial/default/escalate）。开 Gate 后 Run 阻塞；消费后以**新的 Attempt/Lease**
恢复，绝不延续旧进程。

响应 payload 是**类型化契约**（按 Gate 类型 + `outcome` 区分）：缺失/未知 `outcome`、缺失或空白必填
字段、未声明字段、错误类型都会返回稳定错误，Gate 保持 OPEN、Run 不变。合法 outcome 与必填字段：

| Gate | `outcome` | 必填业务字段 |
|---|---|---|
| `GOAL_CLARIFICATION` | `clarified` | 非空 `clarification` |
| `GOAL_CLARIFICATION` | `cancelled` | 无（可选 `reason`） |
| `PLAN_APPROVAL` | `approved` | 无（可选 `comment`） |
| `PLAN_APPROVAL` | `rejected` | 非空 `feedback` |
| `CONFLICT_RESOLUTION` | `resolved` | 非空 `resolution` |
| `CONFLICT_RESOLUTION` | `escalated` | 非空 `reason` |
| `FINAL_REVIEW` | `approved` | 无（可选 `comment`） |
| `FINAL_REVIEW` | `changes` | 非空 `feedback` |

消费在单个 Unit of Work 内原子完成：Gate `RESPONDED`→`CONSUMED`、Run 转换/澄清（`clarified` 把
`clarification` 作为 `effective_goal` 上下文叠加到 `raw_goal` 之上，`raw_goal` 始终不变）、基于原
`state_version` 的**单次 CAS** 保存，以及 `RUN_RESUMED`（状态改变追加 `RUN_STATE_TRANSITION`、终态
追加 `RUN_TERMINATED`）。无法安全应用的响应（continuation 缺失 / 版本 stale / 未知 outcome / 非法
转换）使 Gate 失效（`CANCELED` + `GATE_INVALIDATED`）而不改动 Run。`respond_gate` 同步返回、**不自动
驱动**；消费后需显式 `advance_run` / `drive_run` / `runtime watch` 创建新的执行 claim。

## 恢复（Restart）

所有正式状态在 SQLite；大型内容在不可变、内容寻址的 Artifact Store。进程崩溃后，新进程打开同一
store，`RecoveryManager` 过期陈旧 Lease、重排就绪 Task、检查未知 Operation，Run 从最后一个语义
Checkpoint 恢复并继续。

## 交付物

`report.md`（Markdown）、`report.json`（结构化）、`run-summary.json`（完成度、降级、未达标准、缺失
来源、未决冲突、终止原因）。部分输出会显式披露降级而非伪造成功。

## 配置

通过环境变量 / `.env`（前缀 `ORCH_`），键集与 `.env.example` 完全对齐：存储路径、Web 开关与允许
域名、Model profiles、系统上限（`MAX_TASKS` / `MAX_PLAN_DEPTH` / `MAX_CONCURRENCY` /
`MAX_ATTEMPTS_PER_TASK` / `MAX_REPLANS` / `MAX_REPORT_REVISIONS` / `RUN_DEADLINE_SECONDS`）。
Run Policy 只能在系统上限内收紧。

## 架构分层

```text
CLI → Application → Domain + Runtime Ports ← Infrastructure Adapters
```

Domain 不导入 Typer、SQLite、OpenAI SDK、`agents_memory`、`agents_rag` 或 Web Provider。
同级子项目的公开 API 只允许在各自专用 Adapter 中导入；Secret 只在 Adapter 边界读取。

## 规格一致性（六大能力）

实现覆盖六个能力规格：`durable-orchestration-runtime`、`dynamic-research-planning`、
`research-capability-routing`、`human-gated-orchestration`、`analysis-report-delivery`、
`orchestration-control-surface`。确定性核心（状态机、Plan 校验、Scheduler、Lease/Fencing、Budget、
Gate、Completion 评估、Finalizer）均有测试覆盖；架构测试强制分层与只读边界。

**首期有意延后（不阻塞，需独立 change）**：

- 真实 sibling service（`agents_memory` / `agents_rag`）的 adapter 契约适配与接线：RESEARCH 阶段的多源编排骨架（子问题切分 + LLM 语义源标签 + 多源 Branch 并发 + Phase 2 真并行 + sibling adapter 注入点 `recall_fn`/`query_fn`/`fetch_fn`）已就绪，确定性 fake double 下沉 `tests/support` 验证完整链路；真实 `RecallResult`/`Answer` 契约适配是后续 change；
- 模型型 Worker 的完整 prompt 逻辑（`LLMPlanner`/`LLMAnalyst` 等已接 glm-5.2 function calling，prompt 工程持续打磨）；
- live Smoke 测试默认跳过（`ORCH_LIVE_SMOKE=1` 显式启用）；
- 多 Watch 进程 / 分布式 Worker / FastAPI / Web UI / 真实写副作用 / 完整评测平台。

## 限制

- 首期仅支持单进程持续 Watch；
- 不提供 FastAPI / Web UI / 多租户；
- 不引入真实写副作用；
- 完整 Agent 评测平台在后续独立 change 中建设。
