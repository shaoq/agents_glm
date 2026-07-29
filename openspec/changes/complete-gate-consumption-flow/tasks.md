## 1. 类型化 Gate response contract

- [ ] 1.1 为 4 类 Gate 的 8 个 outcome 定义类型化 response 模型和确定性 validator，拒绝未知字段、错误类型以及必填业务字段缺失/空白
- [ ] 1.2 更新 Gate open 路径，根据 Gate 类型把 canonical response JSON Schema 写入 `allowed_response_schema`
- [ ] 1.3 在 `GateService.respond` 的任何 Gate、dedup 或事件 mutation 前完成 payload 校验，并返回稳定的 `GateResponseError`
- [ ] 1.4 单测全部 8 个合法 outcome，以及缺失 outcome、未知 outcome、缺失业务字段、空白业务字段、错误类型和额外字段

## 2. continuation resolution 与 Run 领域语义

- [ ] 2.1 在 `domain/coordination.py` 增加可判别的 continuation resolution，覆盖 APPLIED、SAME_STATE、MISSING_CONTINUATION、STALE、UNKNOWN_OUTCOME、INVALID_TRANSITION，并让既有 `apply_gate_continuation` 委托该解析逻辑
- [ ] 2.2 为 `Run` 增加可选 `goal_clarification` 和只读 `effective_goal`，保证旧 Run 反序列化以及 `raw_goal` 不变
- [ ] 2.3 定义合法 same-state resume 的 generation bump：状态不变、`state_version` 恰好加一、`updated_at` 更新
- [ ] 2.4 对 `cancelled` 使用 `TerminationReason.CANCELED`，对 `escalated` 使用 `TerminationReason.FAILED`，避免终态 Run 的 termination 为空
- [ ] 2.5 在 `EffectType` 增加 `GATE_INVALIDATED`，专门记录无法安全应用的 Gate 失效而不混用正常消费或 expiry 事件
- [ ] 2.6 单测 4 类 Gate 的全部 outcome、same-state bump、continuation 缺失、stale state/plan binding、未知 outcome、非法转换和终态原因

## 3. 原子 Gate 消费与 durable events

- [ ] 3.1 重构 `application/service.py::respond_gate`：加载 Gate/Run，校验类型化 payload，解析 continuation，再进入正常消费或无法安全应用的失效分支
- [ ] 3.2 正常分支在同一 Unit of Work 中完成 RESPONDED、CONSUMED、Run amendment/转换，并只执行一次 `runs.save(final_run, expected_version=original.state_version)`
- [ ] 3.3 对 MISSING_CONTINUATION、STALE、UNKNOWN_OUTCOME、INVALID_TRANSITION 把 Gate 标记为 CANCELED、记录带 reason 的 `GATE_INVALIDATED` 并提交，Run 不变且不写 RESPONDED/CONSUMED，随后返回稳定错误
- [ ] 3.4 消费成功追加 `RUN_RESUMED`；状态改变时追加 `RUN_STATE_TRANSITION`；终态 outcome 追加 `RUN_TERMINATED`，事件 payload 含 gate/outcome/版本
- [ ] 3.5 集成测试证明 CAS 冲突或中途异常会同时回滚 Gate、Run、dedup claim 和 events
- [ ] 3.6 集成测试证明重复 Request ID 和重复消费仍满足既有 at-most-once/single-use 纪律

## 4. GOAL_CLARIFICATION 业务内容回流

- [ ] 4.1 `GoalPhaseHandler.execute` 改为传递 `ctx.run.effective_goal`，不修改 `GoalNormalizer` protocol 或让 LLM port 依赖 Run/Gate
- [ ] 4.2 消费 `GOAL_CLARIFICATION/clarified` 时把非空 `clarification` 写入最终 Run，且与 same-state resume 共用一次 CAS/version bump
- [ ] 4.3 单测 normalizer 收到“raw goal + clarification”，并断言原始 `raw_goal` 保持不变
- [ ] 4.4 端到端测试：歧义目标 → 开 Gate → clarified response → 原子消费 → 显式 advance/drive → normalize 成功进入 PLANNING，不再循环 BLOCKED

## 5. 公共接口迁移与回归

- [ ] 5.1 把现有 `test_gates`、Gate E2E、恢复测试和所有直接 `GateService.respond` 调用迁移到新的类型化 payload；删除依赖 `{}` 或 `{"ok": true}` 的旧假设
- [ ] 5.2 更新 `agents_orchestration/README.md` 的 CLI/Python API 示例，列出 4 类 Gate 的合法 outcome 和必填业务字段，并说明消费后需要显式 drive/watch
- [ ] 5.3 增加 CLI 测试，覆盖合法 payload、非法 payload 的稳定退出码/诊断以及 response 后 Run 状态查询
- [ ] 5.4 运行 `test_gates`、`test_gate_continuation`、`test_pause_resume_gate_expiry`、Gate E2E 和完整测试套件
- [ ] 5.5 运行覆盖率检查，保证项目既有 80% 阈值继续通过
