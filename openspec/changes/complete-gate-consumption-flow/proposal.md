## Why

Human Gate 当前只完成了“响应并标记消费”，没有把已持久化的 continuation 应用到 Run，也没有把目标澄清作为新的 phase 输入，因此合法响应可能无法推进 Run，或在同一 phase 中重复 BLOCKED。与此同时，响应 payload 仍是未执行的自由字典，无法保证 `clarified` 等 outcome 携带后续 phase 必需的业务内容。

## What Changes

- **BREAKING**：Gate response payload 必须使用按 Gate 类型和 outcome 区分的类型化结构；所有响应都必须显式包含合法 `outcome`，需要反馈内容的 outcome 必须包含非空业务字段。
- 在 `OrchestrationService.respond_gate` 的同一 Unit of Work 中完成响应校验、Gate RESPONDED/CONSUMED、continuation 解析、Run amendment、单次 CAS 保存及事件写入。
- 对 continuation 的 `applied`、`same-state`、`missing`、`stale`、`unknown`、`invalid-transition` 结果做显式分类；合法 same-state resume 也递增 `state_version`，无法安全应用的 Gate 被失效且不修改 Run。
- 为 `Run` 增加可选的目标澄清上下文，并提供 `effective_goal`；Goal phase 使用“原始目标 + 澄清上下文”重新 normalize，`raw_goal` 始终保留。
- 对取消/升级到终态的 outcome 写入正式 `TerminationReason`；消费成功写入 durable `RUN_RESUMED`，状态改变、终止或 stale 失效时补充相应 Run/Gate 事件。
- 补齐 4 类 Gate、8 个 outcome、stale/CAS/非法 payload、GOAL_CLARIFICATION 不循环以及公共 CLI/API 迁移测试。

## Capabilities

### New Capabilities

- `human-gate-consumption-flow`: 定义类型化 Gate 响应、version-bound continuation 消费、原子 Run resume、目标澄清回流以及审计事件的完整契约。

### Modified Capabilities

<!-- 当前 openspec/specs/ 中没有已归档的 Human Gate capability。本 capability 与
     add-intelligent-research-orchestrator 中尚未归档的 human-gated-orchestration
     保持语义一致，但不伪装成对不存在基线 spec 的 MODIFIED delta。 -->

## Impact

- **领域模型**：`domain/execution.py` 增加目标澄清上下文和 `effective_goal`；`domain/coordination.py` 暴露可判别的 continuation resolution；增加类型化 Gate response 模型和 `GATE_INVALIDATED` effect。
- **编排与应用层**：`orchestration/gates.py` 执行类型化 payload 校验；`orchestration/phases.py` 使用 `effective_goal`；`application/service.py` 原子编排 Gate 消费、Run CAS 和事件。
- **公共接口**：CLI/Python API 的 Gate payload 合约发生破坏性变化；README、示例和所有调用方必须迁移。
- **持久化**：Run 仍存于现有 JSON blob，无 SQLite schema migration；旧 Run 缺少新可选字段时可继续反序列化。
- **既有纪律**：single-use、Request ID at-most-once、expiry 和后续新 Attempt/Lease 的执行模型保留；消费成功后仍由显式 `advance/drive/watch` 创建新执行 claim，不在 `respond_gate` 中自动 drive。
- **范围说明**：本 change 补齐消费闭环，不声称修复既有 Human Gate 规格中所有授权模型遗留问题；actor/scope 的领域语义重构及 FINAL_REVIEW 的开 Gate 策略不在本 change 内。
