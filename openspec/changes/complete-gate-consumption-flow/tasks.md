## 1. gap 1：Gate 消费 apply continuation 转 Run state

- [ ] 1.1 在 `application/service.py` 的 `respond_gate`（:306）消费后，调 `apply_gate_continuation(gate, run, outcome, now)`，按 outcome 转 Run state（走 `GATE_CONTINUATION_NEXT`，4 类 gate 都支持）
- [ ] 1.2 转换走 CAS（`uow.runs.save(moved, expected_version=run.state_version)`），保留 stale 版本保护（continuation 绑定漂移时不转，coordination.py:349-352 既有）
- [ ] 1.3 单测：4 类 gate 的 outcome→state 转换（GOAL_CLARIFICATION clarified/cancelled、PLAN_APPROVAL approved/rejected、CONFLICT_RESOLUTION resolved/escalated、FINAL_REVIEW approved/changes）；stale continuation 不转

## 2. outcome 确定性提取 + respond 校验

- [ ] 2.1 定义 gate response payload 结构：含显式 `outcome` 字段（取值限定为 `GATE_CONTINUATION_NEXT[gate_type]` 的 keys）+ 业务内容（如 `clarification`）
- [ ] 2.2 在 `GateService.respond`（gates.py:94）校验 `outcome` 合法（非法/缺失 → `GateResponseError`，不响应/消费）
- [ ] 2.3 单测：合法 outcome 被接受；非法/缺失 outcome 被拒（gate 不变 RESPONDED）

## 3. gap 2：Gate response 业务内容流回 phase（方案 B2）

- [ ] 3.1 `Run` 新增 `clarified_goal` 字段（`str | None`，保留原始 `raw_goal` 不变）——决策 2 的 B2 方案
- [ ] 3.2 `respond_gate` 消费 `GOAL_CLARIFICATION` 时，把 response 的 `clarification` 写入 `Run.clarified_goal`（amend，CAS）
- [ ] 3.3 `LLMGoalNormalizer.normalize`（llm_ports.py:137）读 `clarified_goal or raw_goal`（优先澄清后的目标）
- [ ] 3.4 单测：GOAL_CLARIFICATION 消费后重新 normalize 用上 clarification → PROGRESSED（不循环 BLOCKED）；原始 raw_goal 保留

## 4. 集成 / 回归

- [ ] 4.1 集成测试：GOAL_CLARIFICATION 全链路——歧义→开 gate→respond(clarified+澄清)→消费→转 NORMALIZING→重新 normalize 成功→PLANNING（端到端不循环）
- [ ] 4.2 回归：既有 `test_gates` / `test_gate_continuation` / `test_pause_resume_gate_expiry` 全过（apply_gate_continuation 从 test-only 变生产调用，不应破坏既有行为）
- [ ] 4.3 覆盖率：新增/改动模块达 80%+

## 5. 文档

- [ ] 5.1 更新 `agents_orchestration/README.md`：Gate 工作流说明补上「respond 后消费 → 按 outcome 转 state + 澄清流回」（当前 README 的 Gate 工作流描述可能未含这两步）
