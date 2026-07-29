## Context

深度代码分析（main `d7c8d22`）发现 runtime 存在**两类卡死**，根源都是「执行账本记录了未完成，但没有消费逻辑」：

**卡死 A（task 级）**：`tick._accept_failure`（tick.py:281-311）把 retryable 失败 task 转 `AWAITING_RETRY`（:302），`RetryClassifier`（core.py:112）算出 backoff，但：
- 无代码执行 `AWAITING_RETRY → READY`（state_machine.py:143 定义合法但无调用）
- `Scheduler.ready_work`（core.py:50-53）只选 PENDING/READY
- backoff 只写进 checkpoint reason，无定时器消费
- 结果：可重试失败 task 卡在 AWAITING_RETRY → tick ready 空 + in_flight → blocked → phase IDLE → `drive_run` 空转 → Run 卡死

**卡死 B（phase 级）**：phase 连续返回 IDLE（如 GOAL normalizer 连续失败、或反复缺前置）时，coordinator 存 observation（PREPARED）但 run.state 不变 → `drive_run` 反复 advance 同 phase → 空转到 `max_advances(1000)` 才停。无「有界放弃」机制。

> 原候选「phase execute 幂等复用（ACCEPTED 跳过 provider）」经分析是**伪优化**：原子 accept（coordinator.py:256-279）保证 ACCEPTED 总伴随 state 转换，不会再回到同 phase execute。故不纳入。

## Goals / Non-Goals

**Goals:**

- **候选 1**：task 重试真正工作——backoff 到期后 `AWAITING_RETRY → READY`，Scheduler 重新派发。
- **候选 2'**：phase 级 IDLE 有界放弃——observation 累积超阈值时 Run 明确终止（FAILED + 披露），而非空转。

**Non-Goals:**

- 不改 Lease / Fencing / Checkpoint / Recovery 既有语义。
- 不做「ACCEPTED 复用跳过 provider」（伪优化，触发场景不存在）。
- 不引入真实定时器 / `asyncio.sleep`——backoff 计时基于 `clock` + `task.updated_at`，保持确定性可测。
- 不做 phase 级降级（部分成功）——首期只做「有界 FAILED」，降级留 Open Question。

## Decisions

### 决策 1：候选 1 的 backoff 计时方案——推荐 A（tick Phase 1 加 retry_due）

**方案 A（推荐）**：在 tick 的 Phase 1（dispatch 事务）里、`Scheduler.ready_work` 之前，加一个「重试就绪」步骤：

```text
for task in AWAITING_RETRY tasks of run:
    backoff = base_backoff * 2 ** (task.attempt_count - 1)   # 重算（与 RetryClassifier 一致）
    if now >= task.updated_at + backoff:
        task.transition(READY, now)                          # 持久化
# 然后 Scheduler.ready_work 自然选中新 READY 的 task
```

- backoff **重算**而非读 checkpoint：`task.attempt_count` 已持久化，`task.updated_at` = 转入 AWAITING_RETRY 的时间（tick.py:302 transition 时记）。不需额外存储。
- 复用 `RetryClassifier` 的公式（core.py:119 `base * 2**(attempts-1)`），保证一致。

**Alternatives**：
- **B**：`Scheduler.ready_work` 直接把「backoff 过期的 AWAITING_RETRY」当 ready。缺点：Scheduler 职责扩大（本来只做就绪选择，现在还判 backoff + 改状态），且 Scheduler 当前是纯查询（不改状态）。
- **C**：`RecoveryManager` 处理。缺点：只在进程重启时触发，运行中的 backoff 到期不处理——不解决正常流程的卡死。

**Rationale**：A 把「task 状态转换」集中在 tick（tick 已管 DISPATCHED/AWAITING_RETRY 等转换），Scheduler 保持纯查询。backoff 用 clock 确定性计算，可测。

### 决策 2：候选 2' 的有界放弃——coordinator 在 observation 累积时终止

**方案**：在 `coordinator._accept_observation`（:312）里，存 observation 后查同 `logical_stage` 的 PREPARED 累积：

```text
stages = uow.stages.for_logical_stage(run.run_id, logical_stage_key)   # 已就绪 (repositories.py:469)
pending_obs = [s for s in stages if s.status is PREPARED]
if len(pending_obs) >= threshold:    # threshold = max_attempts_per_task（既有）
    return self._terminate(run, TerminationReason.ATTEMPTS_EXHAUSTED, ...)
```

- 复用既有 `for_logical_stage`（repositories.py:469，已实现）
- 阈值用 `run.policy.max_attempts_per_task`（既有，默认 3），不新增配置
- 走既有 `_terminate`（确定性 policy，CAS），不绕过

**Rationale**：把「phase 反复 IDLE」从「空转到 max_advances」变成「累积超阈值即明确 FAILED」。observation（PREPARED）终于有了消费逻辑（计数 → 终止判断）。

## Risks / Trade-offs

- **[backoff 重算与 RetryClassifier 一致性]** → 决策 1 复用同一公式（`base*2^(attempts-1)`），并加测试断言两者一致。
- **[observation 累积阈值太低 → 误终止]** → 用既有 `max_attempts_per_task`（已含默认 3），语义一致（task 级重试上限 = phase 级放弃上限），且可通过 RunPolicy 收紧/放宽。
- **[有界 FAILED vs 降级]** → 首期只 FAILED（最简、明确）；降级（部分成功继续）留 Open Question。
- **[backoff 上限]** → 指数退避可能很大（attempt_count 高时）；加 cap（如 60s）避免过长等待。Open Question。
- **[observation 计数含历史 PREPARED]** → `for_logical_stage` 返回该 logical 所有 stage（含很久前的）；应只计「当前 fingerprint 相关」或最近 N 条。Open Question。

## Migration Plan

本地开发、无远端，无在线迁移。两处都是 bug 修复：
- 决策 1：tick 加 retry_due 步骤（新增逻辑，不改既有转换）。
- 决策 2'：coordinator `_accept_observation` 加累积检查 + 终止（新增分支）。
- 既有测试（test_runtime / test_recovery）应不受影响（新增逻辑只在 AWAITING_RETRY / observation 累积时触发）。

## Open Questions

- **backoff cap**：指数退避是否加上限（如 60s）？建议加，避免高 attempt_count 时等待过久。
- **observation 计数范围**：`for_logical_stage` 含历史记录，应限定「当前 plan_version / 最近 N 次」？需在实施时定。
- **有界放弃后是否降级**：首期 FAILED；未来可改为「披露降级 + 部分继续」（类似 multi-source 的 OPTIONAL lane 降级）。
- **AWAITING_RETRY 的 failure_code 传递**：重算 backoff 不需 failure_code（已判过 retryable），但 task.failure_code 是否在 READY 后保留用于诊断？建议保留。
