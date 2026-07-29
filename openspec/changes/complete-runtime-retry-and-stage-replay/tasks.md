## 1. 候选 1：Task 重试重新派发（AWAITING_RETRY → READY）

- [x] 1.1 在 `runtime/tick.py` Phase 1（dispatch 事务，`Scheduler.ready_work` 之前）增加「重试就绪」步骤：遍历 run 的 `AWAITING_RETRY` task，计算 `backoff = base * 2^(attempt_count-1)`，若 `now >= task.updated_at + backoff` → `task.transition(READY)` 并持久化
- [x] 1.2 backoff 计算复用 `RetryClassifier` 的公式（core.py:119），抽公共 helper 避免重复，并加 cap（如 60s）防止高 attempt_count 时等待过久（见 Open Questions）
- [x] 1.3 单测：backoff 到期 → READY + 被 Scheduler 选中；未到期 → 保持 AWAITING_RETRY；backoff 公式与 RetryClassifier 一致
- [x] 1.4 集成测试：retryable 失败 task → AWAITING_RETRY → backoff 过 → 重新派发 → 最终 SUCCEEDED（全链路不卡死）

## 2. 候选 2'：Phase IDLE 有界放弃

- [x] 2.1 在 `orchestration/coordinator.py` 的 `_accept_observation`（:312）存 observation 后，查 `uow.stages.for_logical_stage(run.run_id, logical_stage_key)`（已就绪，repositories.py:469），统计 `status=PREPARED` 数量
- [x] 2.2 若 PREPARED 累积 `>= run.policy.max_attempts_per_task` → 走既有 `_terminate(run, ATTEMPTS_EXHAUSTED)`（CAS + Event + Checkpoint），返回 TERMINAL
- [x] 2.3 单测：observation 累积达阈值 → Run FAILED（ATTEMPTS_EXHAUSTED），不空转；未达阈值 → 继续 IDLE
- [x] 2.4 集成测试：phase 连续 IDLE（如 GOAL normalizer 连续失败）超阈值 → Run 有界 FAILED（而非 drive_run 空转到 max_advances）

## 3. observation 计数范围与回归

- [x] 3.1 确认 `for_logical_stage` 的计数范围（是否限定当前 plan_version / 最近 N 条，见 Open Questions），避免历史 observation 误计入；必要时加过滤
- [x] 3.2 回归：既有 `test_runtime` / `test_recovery` / `test_phase_*` 全过（新逻辑只在 AWAITING_RETRY / observation 累积时触发，不应破坏既有行为）
- [x] 3.3 覆盖率：新增/改动模块达 80%+

## 4. 文档

- [x] 4.1 更新 `agents_orchestration/README.md`：说明 task 重试真正生效 + phase 有界放弃（替换「max_concurrency 名副其实」相关描述里对重试的隐含假设，如有）
