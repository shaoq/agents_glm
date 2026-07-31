## 1. Plan、Boundary 与 Action 契约

- [ ] 1.1 为 `fixed_fanout | agent_loop` 定义持久化 `ResearchExecutionMode` 和 Plan `schema_version`，旧 Plan 缺失 mode 时确定性解释为 `fixed_fanout`
- [ ] 1.2 定义 frozen `ExplorationBoundary`：allowed capabilities、required coverage by seed、每 seed max_steps/max_directions 与可选 token/cost ceiling
- [ ] 1.3 扩展 PlanProposal/PlanGraph 的 mode/boundary 序列化，并保证旧 JSON Plan 可读取、同一 Plan version 的 mode 不可变
- [ ] 1.4 定义强类型 `QUERY` / `ADD_DIRECTION` / `STOP_REQUEST` union 及携带 run/plan/task/loop/step identity 的 envelope
- [ ] 1.5 定义只读、有界 `ResearchLoopView` 与 ResearchAgent Port；模型输出不得携带 capability ID、request ID、状态或持久化 mutation
- [ ] 1.6 增加模型/validator 测试：空 seed、Boundary 越界、required coverage 非子集、非法 action 和正式状态注入均在首次写入前失败

## 2. Durable Loop、Direction 与 Step 持久化

- [ ] 2.1 定义 `ResearchLoop` aggregate：Plan/Task binding、status、next step index、accepted Evidence IDs、coverage、step/direction/usage counters
- [ ] 2.2 定义 `ResearchDirection`：parent、sanitized text、focus hash、capability scope、source step 与 plan/task binding
- [ ] 2.3 定义 `ResearchStep`：stable logical ID、DECIDING/PREPARED/ACCEPTED/FAILED status、独立 decision/capability request IDs、reservation/usage、retry count、Attempt/Lease binding 和结果 refs
- [ ] 2.4 增加 SQLite schema、repositories、UoW ports 与 CAS/unique constraints；logical step 和 focus hash 去重必须由持久层约束
- [ ] 2.5 增加事务测试：Step/Evidence/usage/Direction/Event/Checkpoint 任一写入或 CAS 失败时全部回滚
- [ ] 2.6 增加 replay 测试：相同 logical step、request ID 或 focus hash 最多接受一次且不双重扣费

## 3. Planner、PlanValidator 与 PLAN_APPROVAL

- [ ] 3.1 扩展 Planner structured output，使其输出非空 seed Tasks、execution mode 与 ExplorationBoundary；继续只允许 `evidence_researcher`
- [ ] 3.2 保留 source_hints 的确定性映射，并将 agent-loop Task 的 required capabilities 解释为最低 coverage，而非仅 allowlist
- [ ] 3.3 扩展 SystemLimits/RunPolicy 与 PlanValidator，对 mode、seed、capability、per-seed coverage/steps/directions/ceiling 及有限 Run budget 下的最坏总和做写入前完整校验
- [ ] 3.4 保证 PlanAcceptor 在一个事务中接受 Plan/boundary、materialize seed Tasks、更新 Run 与事件；拒绝路径零部分写入
- [ ] 3.5 扩展 PLAN_APPROVAL payload/rendering，分别展示 seed、allowed capability、每 seed required coverage/steps/directions/budget ceiling 和固定下游生命周期
- [ ] 3.6 Gate continuation 绑定 seed/boundary hash；Plan boundary 变化后旧 response 必须失效，批准后使用持久化 mode/boundary 恢复

## 4. 共享 Direction 安全策略与 ActionValidator

- [ ] 4.1 从 FocusedReplanBuilder 抽取无副作用 `ResearchDirectionPolicy`，共享 control-char 清洗、长度上限、untrusted label、gap/focus hash 与 capability narrowing
- [ ] 4.2 保持现有 FocusedReplanBuilder 输出和宏观 Plan v+1/replan_count 行为不变，并以回归测试证明
- [ ] 4.3 实现 ADD_DIRECTION 接受：只创建 loop-local Direction，不创建 Task/Plan/Event transition，不修改 Run/current_plan_version/replan_count
- [ ] 4.4 实现 focus hash 去重；重复 Direction 记录 dedup 事件且不消耗 max_directions
- [ ] 4.5 实现 ActionValidator：identity、step、direction、capability、query/hint 长度、coverage、budget 和 boundary expansion 校验
- [ ] 4.6 增加恶意输入测试：Evidence/hint 中的指令、capability 枚举、状态跳转和预算扩展文本都不能改变正式 routing/state

## 5. RuntimeTick 单步 prepare/execute/accept

- [ ] 5.1 先增加失败测试：一个 agent-loop Task 在单次 tick 内不得执行两个 steps 或两个 capability operations
- [ ] 5.2 扩展 RuntimeTick dispatch，为每个 selected agent-loop Task claim Lease、创建 Attempt，并在任何 Agent 调用前原子持久化 DECIDING ResearchStep、reasoning reservation 与稳定 decision request ID
- [ ] 5.3 Agent decide 后用短事务对账 decision request、接受实际 reasoning usage/释放 reservation、持久化 typed action 并转为 PREPARED；capability 调用前再次验证 Attempt/Lease/Plan/Run/step fencing
- [ ] 5.4 QUERY 使用由 logical step 派生的确定性 request ID，经 registry + CapabilityRouter 调用至多一个 capability
- [ ] 5.5 ADD_DIRECTION 与 STOP_REQUEST 不调用 capability；三类 action 共用统一 structured outcome
- [ ] 5.6 Accept 事务原子保存 Step、Evidence、usage、coverage/Direction、事件、checkpoint 并释放 Lease
- [ ] 5.7 QUERY/ADD_DIRECTION 成功后把 Task 从 DISPATCHED 重新置为可调度非终态；LoopGuard 接受 STOP_REQUEST 后才置 SUCCEEDED
- [ ] 5.8 保留 fixed_fanout 原路径；RuntimeTick 根据持久化 Plan mode 分流，不根据进程级 feature flag 改变 active Plan
- [ ] 5.9 保留跨 seed Task `asyncio.gather` + Semaphore 并行，证明同 Task 单 step 串行且不同 Task 可并行

## 6. Step Retry、Lease Heartbeat 与逐步预算

- [ ] 6.1 为 agent-loop retry admission 使用 `ResearchStep.retry_count`；Task 历史 dispatch/attempt_count 只作审计，不耗尽后续 step 的 retry
- [ ] 6.2 增加后期失败回归：多个成功 steps 后的新 step 第一次 retryable failure 仍可按 step budget 重试
- [ ] 6.3 实现执行期 Lease heartbeat（默认 TTL/3），续期仍使用相同 epoch；续期失败或 lease 被替换后结果必须 fenced
- [ ] 6.4 定义 Agent reasoning 与 capability 的预算 reservation/实际 usage contract，并将 loop ceiling 约束为共享 Run budget 子集
- [ ] 6.5 在 decide/query 前做 deadline、Run remaining、loop remaining、step/direction 上限准入；accept 时原子消费实际 usage 并释放未用 reservation
- [ ] 6.6 防止 RuntimeTick final TaskResult 再次累计已逐步消费的 usage；增加多 step 总账精确相等和 replay 不双计测试
- [ ] 6.7 区分终止：global budget/deadline → terminal reason；loop ceiling/max_steps/max_directions → EXHAUSTED + Degradation 后进入 ANALYZE

## 7. STOP Guard、Evidence Join 与 ANALYZE 边界

- [ ] 7.1 实现确定性 LoopGuard：required coverage、独立 Evidence、supporting IDs ownership、无 in-flight step 和计数一致性
- [ ] 7.2 STOP_REQUEST 被拒绝时记录结构化原因并重新排队；不得进入 ANALYZING/WRITING
- [ ] 7.3 STOP_REQUEST 被接受或 loop EXHAUSTED 时关闭该 seed loop，并保留 status/degradation/coverage
- [ ] 7.4 ResearchPhaseHandler 只在所有 required seed loops 关闭后加载已接受 Evidence、确定性 join 并进入 ANALYZING
- [ ] 7.5 保持 ANALYZE L0/L1 现有契约不变：STOP 不直接调用或替代依赖 AnalysisArtifact 的 sufficiency reviewer
- [ ] 7.6 回归证明 ANALYZE 判 gap 时才走正式 Focused Replan：Plan v+1、replan_count+1、新 Task 和原子 transition

## 8. Recovery、幂等与不可信上下文

- [ ] 8.1 为 DECIDING/PREPARED step 实现 restart recovery：Agent 未调用/已知完成/unknown outcome、capability 未调用/已知完成/unknown outcome 和 expired Lease 分别确定性处理
- [ ] 8.2 外部调用成功但 accept 前崩溃时，用稳定 request ID/operation repository reconciliation，禁止重复接受 Evidence/usage
- [ ] 8.3 旧 Plan/Run/Lease/step result 作为 observation 保留但不能改变当前 Evidence、Direction、budget、Task 或 Run
- [ ] 8.4 LoopView 对 Evidence 做来源标记、untrusted framing、长度/数量上限、去重和有界 digest；不拼接无限原文
- [ ] 8.5 为 Agent provider failure、invalid response、Router denial、capability retryable/non-retryable failure定义 FailureCode、retry/degrade/terminate 映射
- [ ] 8.6 Pause/Resume 恢复相同 Plan/Loop/next step，创建新执行 claim，且不重置 steps/directions/coverage/usage/retry history

## 9. Composition、兼容模式与可观测性

- [ ] 9.1 Production composition 注入 ResearchAgent、ActionValidator、DirectionPolicy、Loop repositories 和 heartbeat/budget services，不接真实 sibling adapters
- [ ] 9.2 配置只决定“新 Plan 默认 mode”；active Plan 始终按持久化 mode/schema version 消费
- [ ] 9.3 缺失 mode 的 legacy Plan 走 fixed_fanout；增加升级中 active Run 继续完成的集成测试
- [ ] 9.4 增加结构化 loop/step/action/query/direction/stop/exhaustion 事件，包含安全 identity、coverage、usage 和诊断
- [ ] 9.5 Control surface/run show/watch 展示 execution mode、boundary、loop progress、step/coverage/usage，不输出原始敏感 prompt 或完整外部正文
- [ ] 9.6 Rollback 测试：切回 fixed_fanout 只影响新 Plan，已存在 agent-loop Plan 仍由兼容 consumer 完成或显式取消

## 10. 契约、故障注入与端到端验证

- [ ] 10.1 为固定 action sequence 构建 deterministic ResearchAgent double，并覆盖 QUERY → observe → ADD_DIRECTION → QUERY → STOP_REQUEST
- [ ] 10.2 契约测试覆盖 max_steps、max_directions、loop/global budget、required coverage、capability narrowing、Direction dedup 和 prompt injection
- [ ] 10.3 故障注入覆盖 decide 后、PREPARED 后、capability 成功后、Evidence 写入中、usage 写入中、Task CAS 前和 Lease 续期失败
- [ ] 10.4 并发测试覆盖多个 seed Tasks 并行、同 Task 单 step、stale worker、Plan v+1 fencing 和 recovery 后无重复 operation
- [ ] 10.5 E2E：Evidence A 揭示未预声明方向 B，loop-local B 在相同 Plan version 内产生新增 Evidence，STOP 后经 ANALYZE/WRITE/REVIEW/FINALIZE 完成
- [ ] 10.6 E2E：过早 STOP 被拒绝，边界耗尽后 ANALYZE 判 gap，再由宏观 Focused Replan 创建 Plan v+1 并完成第二轮研究
- [ ] 10.7 回归 fixed_fanout、多源 required/optional degradation、RuntimeTick 非 RESEARCHING 零派发、Gate 与 existing sufficiency closed loop
- [ ] 10.8 运行完整 offline pytest、ruff、覆盖率阈值、`openspec validate add-research-agent-loop --strict` 和 `git diff --check`
- [ ] 10.9 实施完成且提交前运行 canonical GitNexus `detect-changes`，确认影响仅覆盖预期 Plan/Runtime/Research/Gate 执行流
