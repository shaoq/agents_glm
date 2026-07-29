## ADDED Requirements

### Requirement: Task 重试重新派发（AWAITING_RETRY → READY）

系统 SHALL 在 backoff 到期后将 `AWAITING_RETRY` 的 task 转为 `READY`，使其被 `Scheduler` 重新选中派发。backoff 基于确定性计算（`base * 2^(attempt_count-1)`，与 `RetryClassifier` 一致），以 `task.updated_at`（转入 AWAITING_RETRY 的时间）为起点，不引入真实定时器。

#### Scenario: backoff 到期后重新派发

- **WHEN** 一个 `AWAITING_RETRY` 的 task，`now >= task.updated_at + backoff`
- **THEN** tick 将其转为 `READY`，`Scheduler.ready_work` 选中并派发新 Attempt

#### Scenario: backoff 未到期不重新派发

- **WHEN** 一个 `AWAITING_RETRY` 的 task，`now < task.updated_at + backoff`
- **THEN** task 保持 `AWAITING_RETRY`，本轮不被派发

#### Scenario: 可重试失败最终成功

- **WHEN** 一个 task 因 retryable 失败（如 `UPSTREAM_ERROR`）进入 `AWAITING_RETRY`
- **AND** backoff 到期后重新派发，新 Attempt 成功
- **THEN** task 最终 `SUCCEEDED`，Run 继续推进（而非卡死）

#### Scenario: 重试预算用尽仍 FAILED（不回归）

- **WHEN** 一个 `AWAITING_RETRY` 的 task 重派后再次失败，且 `attempt_count >= max_attempts_per_task`
- **THEN** task 转 `FAILED`（既有 RetryClassifier 语义，本 change 不回归）

### Requirement: Phase IDLE 有界放弃（observation 累积 → 终止）

系统 SHALL 在某 `logical_stage` 的 observation（`StageExecution.status=PREPARED`）累积数量达到阈值（`run.policy.max_attempts_per_task`）时，将 Run 有界终止为 `FAILED`（`ATTEMPTS_EXHAUSTED`），而非让 `drive_run` 空转到 `max_advances`。终止走既有确定性 `_terminate`（CAS），并披露降级。

#### Scenario: observation 累积超阈值终止

- **WHEN** 某 phase 连续返回 IDLE，同 `logical_stage` 的 PREPARED observation 数量 `>= max_attempts_per_task`
- **THEN** Run 终止为 `FAILED`（`ATTEMPTS_EXHAUSTED`），`drive_run` 不再空转

#### Scenario: observation 未超阈值继续重试

- **WHEN** 某 phase 返回 IDLE，同 `logical_stage` 的 PREPARED observation 数量 `< max_attempts_per_task`
- **THEN** Run 继续（下轮 advance 正常重试），不终止

#### Scenario: 终止原因可观测

- **WHEN** Run 因 observation 累积被有界终止
- **THEN** 记录 `RUN_TERMINATED` Event（`ATTEMPTS_EXHAUSTED`）+ Checkpoint，可在 `run-summary.json` 披露
