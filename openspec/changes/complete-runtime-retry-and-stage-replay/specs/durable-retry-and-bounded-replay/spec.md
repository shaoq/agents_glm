## ADDED Requirements

### Requirement: Task 重试重新派发（AWAITING_RETRY → READY）

系统 SHALL 在 backoff 到期后将 `AWAITING_RETRY` 的 task 转为 `READY`，使其被 `Scheduler` 重新选中派发。backoff 基于确定性计算（`base * 2^(attempt_count-1)`，与 `RetryClassifier` 一致），以 `task.updated_at`（转入 AWAITING_RETRY 的时间）为起点，不引入真实定时器。

#### Scenario: backoff 到期后重新派发

- **WHEN** 一个 `AWAITING_RETRY` 的 task，`now >= task.updated_at + backoff`
- **THEN** tick 将其转为 `READY`，`Scheduler.ready_work` 选中并派发新 Attempt

#### Scenario: backoff 未到期不重新派发

- **WHEN** 一个 `AWAITING_RETRY` 的 task，`now < task.updated_at + backoff`
- **THEN** task 保持 `AWAITING_RETRY`，本轮不被派发
- **AND** phase 将该状态标记为 WAITING，`drive_run` 让出控制且不消耗 phase IDLE 预算

#### Scenario: 可重试失败最终成功

- **WHEN** 一个 task 因 retryable 失败（如 `UPSTREAM_ERROR`）进入 `AWAITING_RETRY`
- **AND** backoff 到期后重新派发，新 Attempt 成功
- **THEN** task 最终 `SUCCEEDED`，Run 继续推进（而非卡死）

#### Scenario: Attempt 接纳后释放 Lease

- **WHEN** 一个 Attempt 的成功或失败结果被接纳
- **THEN** 系统在同一事务内释放与该 Attempt/epoch 匹配的 active Lease
- **AND** 重试重派后每个 task 至多保留一个 active Lease，旧 epoch 到期不会阻塞 Recovery

#### Scenario: 重试预算用尽仍 FAILED（不回归）

- **WHEN** 一个 `AWAITING_RETRY` 的 task 重派后再次失败，且 `attempt_count >= max_attempts_per_task`
- **THEN** task 转 `FAILED`（既有 RetryClassifier 语义，本 change 不回归）

### Requirement: Phase IDLE 有界放弃（observation 累积 → 终止）

系统 SHALL 在某 `logical_stage` 的 observation（`StageExecution.status=PREPARED`）累积数量达到阈值（`run.policy.max_attempts_per_task`）时，将 Run 有界终止为 `FAILED`（`ATTEMPTS_EXHAUSTED`），而非让 `drive_run` 空转到 `max_advances`。终止走既有确定性 `_terminate`（CAS），并披露降级。

#### Scenario: observation 累积超阈值终止

- **WHEN** 某 phase 连续返回 IDLE，同 `logical_stage` 的 PREPARED observation 数量 `>= max_attempts_per_task`
- **THEN** Run 终止为 `FAILED`（`ATTEMPTS_EXHAUSTED`），`drive_run` 不再空转

#### Scenario: observation 只统计当前连续失败

- **WHEN** 同一 `logical_stage` 存在历史 fingerprint、BLOCKED、stale 或 WAITING observation
- **THEN** 这些记录不计入当前 fingerprint 的连续可计费 IDLE 次数
- **AND** 任一非可计费 observation 会中断连续计数

#### Scenario: 缺失 fingerprint 的 IDLE 仍有界

- **WHEN** phase 因缺少前置数据返回 IDLE 且未提供 `input_fingerprint`
- **THEN** Coordinator 基于当前 Run/Plan 版本生成确定性 fingerprint 并记录 observation
- **AND** 连续次数达到阈值后 Run 仍以 `ATTEMPTS_EXHAUSTED` 终止

#### Scenario: observation 未超阈值继续重试

- **WHEN** 某 phase 返回 IDLE，同 `logical_stage` 的 PREPARED observation 数量 `< max_attempts_per_task`
- **THEN** Run 继续（下轮 advance 正常重试），不终止

#### Scenario: 终止原因可观测

- **WHEN** Run 因 observation 累积被有界终止
- **THEN** 记录 `RUN_TERMINATED` Event（`ATTEMPTS_EXHAUSTED`）+ Checkpoint，可在 `run-summary.json` 披露
