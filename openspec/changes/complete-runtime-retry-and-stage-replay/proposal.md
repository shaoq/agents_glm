## Why

深度代码分析（基于 main `d7c8d22`）发现 runtime 的「执行账本」**写入侧完整、消费/终止侧未接**，导致**两类卡死**——一个是 task 级、一个是 phase 级，同一种病症的两种表现：

1. **task 重试重新派发缺失（功能性 bug，高优先）**：可重试的失败 task 卡在 `AWAITING_RETRY`，永远不会被重新派发——`RetryClassifier` 的判定与指数 backoff 形同摆设，Run 卡在 RESEARCHING 无法终态。
2. **phase 级 IDLE 死循环（功能性 bug，高优先）**：phase 连续返回 IDLE（observation/PREPARED 累积）时，`drive_run` 会空转到 `max_advances(1000)` 才停——Run 长时间卡死、无有界放弃机制。例如 GOAL normalizer 连续失败、或某 phase 反复 IDLE。

共同根源：**执行账本（`AWAITING_RETRY` task 状态、`StageExecution` observation）已记录「未完成」，但没有「据此重新执行」或「据此有界放弃」的消费逻辑**。

> 说明：原候选「phase execute 幂等复用（ACCEPTED 跳过 provider）」经分析确认是**伪优化**——当前原子 accept 保证 ACCEPTED 总伴随 state 转换，不会再回到同 phase execute，复用场景不存在。故改为更实际的「phase 有界放弃」。

## What Changes

- **候选 1：task 重试重新派发**：实现 backoff 到期后 `AWAITING_RETRY → READY`，让 `Scheduler` 重新选中重试 task。修复可重试失败 task 卡死，使 `RetryClassifier` + 指数 backoff 真正生效。
- **候选 2'：phase IDLE 有界放弃**：跟踪同 `logical_stage` 的 observation（PREPARED）累积次数，超过阈值（如 `max_attempts_per_task`）时**有界终止**（Run → FAILED，`ATTEMPTS_EXHAUSTED`，并披露降级），避免 `drive_run` 空转到 `max_advances`。让 phase 级死循环变成明确的「有界失败」。
- **配套测试**：重试 task 在 backoff 后重新派发并最终 SUCCEEDED；phase 连续 IDLE 超阈值后 Run 有界 FAILED（而非空转）。

## Capabilities

### New Capabilities

- `durable-retry-and-bounded-replay`: task 重试重新派发（确定性 backoff 计时 + `AWAITING_RETRY → READY` + Scheduler 选中）与 phase 级 IDLE 有界放弃（observation 累积超阈值 → 有界终止/降级）。

### Modified Capabilities

<!-- 现有 openspec/specs/ 下的 capability 均属 RAG/Memory 检索能力，本 change 不改变其 spec 级需求。 -->

## Impact

- **代码**：`runtime/tick.py` + `runtime/core.py`（Scheduler / backoff 计时 / AWAITING_RETRY→READY）（候选 1）；`orchestration/coordinator.py`（`_accept` 跟踪 observation 累积 → 有界终止）（候选 2'）；可能读 `runtime/persistence/repositories.py` 的 `for_logical_stage`（已就绪，:469）。
- **既有纪律不变**：Lease / Fencing / Checkpoint / Recovery 语义不动；有界终止仍走确定性 policy（`_terminate`，不绕过 CAS）；backoff 计时确定性（基于 `clock` + checkpoint 时间戳，不引入真实定时器 / sleep）。
- **向后兼容**：两者都是 bug 修复——之前卡死的 Run 现在能完成（成功或明确失败）。
