## ADDED Requirements

### Requirement: Gate 消费按 outcome 转 Run state

系统 SHALL 在 Gate 消费时调用 `apply_gate_continuation`，按 gate response 的 outcome 与 `GateContinuation.next_state_by_outcome` 将 Run 转到对应状态，覆盖全部 4 类 Gate。转换仍走 CAS（`run.save expected_version`），且保留 stale 版本保护（continuation 绑定的 `state_version`/`plan_version` 漂移时不转）。

#### Scenario: GOAL_CLARIFICATION 澄清后重新 normalize

- **WHEN** `GOAL_CLARIFICATION` gate 的 response outcome = `clarified`
- **THEN** Run 转到 `NORMALIZING`（按 continuation），重新 normalize

#### Scenario: GOAL_CLARIFICATION 取消

- **WHEN** `GOAL_CLARIFICATION` gate 的 response outcome = `cancelled`
- **THEN** Run 转到 `CANCELED`（终态）

#### Scenario: PLAN_APPROVAL 批准进入研究

- **WHEN** `PLAN_APPROVAL` gate 的 response outcome = `approved`
- **THEN** Run 转到 `RESEARCHING`

#### Scenario: stale continuation 不推进

- **WHEN** Gate continuation 绑定的 `state_version`/`plan_version` 与当前 Run 不符（如期间发生过 replan）
- **THEN** `apply_gate_continuation` 不转 state（既有保护，不回归）

### Requirement: outcome 确定性提取

Gate response payload SHALL 包含显式 `outcome` 字段（取值限定为该 gate 类型在 `GATE_CONTINUATION_NEXT` 中的 keys）。`GateService.respond` MUST 校验 `outcome` 合法——非法值拒绝消费（`GateResponseError`），不依赖 LLM 或 schema 推断。

#### Scenario: 合法 outcome 被接受

- **WHEN** response payload 的 `outcome` 在该 gate 类型的合法集合内
- **THEN** gate 进入 RESPONDED，后续消费按该 outcome 转 state

#### Scenario: 非法 outcome 被拒绝

- **WHEN** response payload 的 `outcome` 不在合法集合内（或缺失）
- **THEN** `GateService.respond` 抛 `GateResponseError`，gate 不被响应/消费

### Requirement: Gate response 业务内容流回 phase

`GOAL_CLARIFICATION` 的澄清业务内容 SHALL 流回 phase——消费时并入 Run 的目标信息（澄清后的目标），使重新 normalize 时 normalizer 能读到，避免基于原 `raw_goal` 再次判歧义而循环 BLOCKED。

#### Scenario: 澄清后重新 normalize 成功

- **WHEN** 用户对 `GOAL_CLARIFICATION` 提交 `{outcome: clarified, clarification: "..."}` 且被消费
- **AND** Run 转 `NORMALIZING` 后重新 normalize
- **THEN** normalizer 读到并入澄清后的目标，归一化成功 → PROGRESSED（不再循环 BLOCKED）

#### Scenario: 原始 raw_goal 保留

- **WHEN** 澄清并入 Run 目标信息
- **THEN** 原始 `raw_goal` 保留不变（审计可追溯，澄清作为叠加而非覆盖）
