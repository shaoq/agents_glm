## Why

`agents_orchestration` 已实现 Goal、Plan、Task Runtime、Evidence、Gate、Report 和 Finalizer 等组件，
但当前公开入口只会创建 Run 并驱动已经存在的 Ready Task，不能从原始研究目标自动推进到最终报告。
需要增加 Run 级生命周期协调层，将已有组件组合为可恢复、可阻塞、可终止的完整应用流程。

## What Changes

- 增加状态驱动的 `RunCoordinator`，按 Run 当前状态执行一个有界语义步骤，并统一提交状态、事件和
  Checkpoint。
- 增加 Normalize、Plan、Research/Join、Analyze、Write、Review 和 Finalize 阶段处理器，将现有领域
  组件接入正式运行路径。
- 让 Runtime Watch 循环驱动 RunCoordinator；保留 Runtime Tick 作为 Ready Task 的派发、执行与结果
  接受单元。
- 增加面向用户的统一“创建并驱动”入口，同时保留明确的 create-only 与单步推进能力。
- 持久化 Gate 的 continuation phase，使响应、进程重启和 Resume 都能恢复到确定的下一阶段，而不是
  由调用者任意指定目标状态。
- 建立完整 Composition Root，显式注入 Goal Normalizer、Planner、阶段 Worker、Capability Adapter、
  Reviewer、Report Builder 和 Finalizer；测试默认继续使用确定性 Fake 实现。
- 用仅调用公开入口的真正端到端测试验证 Goal 到三个最终 Artifact 的完整闭环，并覆盖 Gate、Replan、
  Revision、Pause/Resume、重启恢复、终止和降级路径。
- 对齐 CLI、Python API 与现有规格语义：`run start` 默认创建并驱动，`--create-only` 只持久化，
  Runtime 运维命令不绕过 RunCoordinator 的阶段规则。

## Capabilities

### New Capabilities

- `run-lifecycle-coordination`: 定义 Run 级状态驱动协调、阶段处理、统一入口、Gate continuation、恢复和
  端到端完成语义。

### Modified Capabilities

- None.

## Impact

- 主要影响 `agents_orchestration/application`、`runtime/watch.py`、CLI composition root，以及新增的
  Run 协调与阶段处理模块。
- 复用现有 Domain、Planner、WorkerExecutor、RuntimeTick、Gate、Evidence Join、Report 和持久化端口；
  不改变 `agents_memory`、`agents_rag` 的公开 API 或依赖方向。
- SQLite 需要持久化协调进度和 Gate continuation 所需的最小状态；迁移必须兼容已有 Run 数据。
- 现有手工拼接 Plan 和 Finalizer 的 E2E 测试将被公开入口 E2E 替代或降级为组件集成测试。
- 现有同步 `start_run(raw_goal, request_id=...)` 暂保留为兼容的创建入口并标记弃用；新的统一入口负责
  create-and-drive，避免在同一发布中破坏已有调用方。
