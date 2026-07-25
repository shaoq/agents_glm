## Context

`agents_memory` 已实现独立的 Write Pipeline，当前以 SQLite 作为记忆状态真相源、Chroma 作为派生语义索引，并通过 GLM-4.7-Flash 和 Embedding 完成语义处理。现有 `retrieval/lookup.py` 服务于写入查重和关系判断，只支持精确 Scope、单一类型及相似度阈值，不具备面向任务的分层召回、时序解析、冲突保留和集合级上下文组装语义。

召回知识模型已经在 `agents_memory/docs/knowledge/memory-recall-pipeline.md` 中完成讨论，完整实施设计记录于 `agents_memory/docs/specs/2026-07-25-memory-recall-pipeline-implementation.md`。本 change 将该设计转化为可测试的增量实现。

约束：

- `agents_memory` 必须保持独立，不能依赖 `agents_rag` 等同级子项目；
- Recall 首期由调用方显式触发，不进入 Agent 自动编排；
- Recall 主路径只读，不修改记忆、索引状态或待处理项；
- SQLite 决定真实状态，Chroma 只生成候选；
- 现有 Write Pipeline 和公共维护命令必须保持兼容；
- 安全专项和完整评测专项不在本 change 展开；
- 修改任何现有符号前必须执行 GitNexus upstream impact，提交前必须执行 change detection。

## Goals / Non-Goals

**Goals:**

- 建立从 `RecallRequest` 到结构化 `RecallResult` 的七阶段 Recall 管线；
- 支持当前会话、Agent 历史、用户共享三层且严格用户隔离的候选召回；
- 组合语义、结构化时间、未同步覆盖和关系扩展路径；
- 将资格过滤、单条效用、证据解析和集合选择拆成明确阶段；
- 正确表达当前、历史、演化、修正、冲突和未知事件身份；
- 在数量及 Token 预算内生成可追溯、低冗余的上下文；
- 对 LLM、Embedding、Chroma 和部分通道故障提供可验证降级；
- 通过 Service 和 CLI 提供同一能力；
- 以加法方式扩展现有基础设施，避免 Write 回归。

**Non-Goals:**

- 自动判断每轮是否需要 Recall；
- HTTP API 或独立服务部署；
- Recall 使用反馈、访问频率或热度写回；
- 自动纠正、合并、删除或创建记忆；
- 创建 Deferred/Pending 或要求用户澄清；
- Reflection 自动调度；
- 自主多轮检索和无限关系遍历；
- 生成式长文本压缩；
- Agent 间细粒度可见性；
- 完整安全治理和召回评测体系；
- 跨子项目代码、配置或存储复用。

## Decisions

### 1. 采用分阶段混合管线

管线顺序固定为：

```text
intent
→ planning/retrieval
→ filtering
→ scoring
→ evidence resolution
→ set selection
→ context assembly
```

`MemoryRecallPipeline` 只编排并聚合诊断，各阶段通过结构化对象交互。

**理由：** Recall 的硬边界、语义判断、候选间关系和预算选择具有不同失败语义。分阶段设计可以分别测试、降级和解释，也与现有 Write Pipeline 的模块化方向一致。

**备选方案：**

- “检索后一次 LLM 综合裁决”代码更少，但排序、冲突和组装无法独立验证；
- “LLM 规划式迭代召回”更灵活，但首期的调用次数、延迟和终止条件不可控。

### 2. 对外稳定契约为 `RecallRequest` / `RecallResult`

调用方提供原始任务上下文，不需要预构造 `RecallIntent`。结果同时包含 evidence、context 和 metadata。

内部阶段契约放入 Recall 专属模型模块，组合现有 `MemoryRecord`，不向存储模型写入临时召回字段。

**理由：** 结构化证据是可解释、调试、评测和未来 Reflection 的基础；纯文本输出会丢失来源和冲突边界。

**备选方案：**

- 只返回 context 使用简单，但不可追溯；
- 只返回 MemoryRecord 列表会把冲突和组装责任泄漏给调用方。

### 3. 由调用方决定触发，Recall 内部构建意图

`MemoryService.recall(...)` 是显式入口。Intent Builder 使用当前查询和有限消息窗口，输出少量查询变体、时间需求、目标类型和关系需求。

身份、授权、硬预算和显式限制由确定性代码控制。Intent LLM 失败时使用原始查询生成保守意图。

**理由：** 把“何时召回”留在 Agent 编排层可避免 `agents_memory` 与特定对话循环耦合，同时保留完整 Recall 能力。

### 4. 同一用户内采用三层候选通道

Planner 生成：

- `session_current`；
- `agent_history`；
- `user_shared`。

每个通道有独立配额，候选阶段提供通道保底，最终选择不强制平均覆盖。调用方可关闭更宽通道。

**理由：** 写入的精确归属和召回的合法扩展是不同问题。分路避免当前会话被长期记录淹没，同时保留跨会话价值。

**权衡：** 当前模型没有 Agent 间持久化可见性。首期接受同一用户内共享，未来需要私有性时新增持久化 visibility，而不是调整评分。

### 5. SQLite 真相源，Chroma 候选源

Chroma 结果必须按 ID 批量回查 SQLite。SQLite 不可用时 Recall 终止；Chroma 不可用时使用有界 SQLite 回退。

未同步 ACTIVE 记录通过有限覆盖集参与当前 Recall，但 Recall 不执行索引修复。

**理由：** 派生索引可能延迟写入或延迟删除，不能决定有效性和授权。

### 6. 不复用写入 `ContextLookup`

Recall 新建候选层，只共享 Embedder、MemoryIndex、Repository 和通用模型。

**理由：** `ContextLookup` 的目标是写入去重，现有精确 Scope、类型和阈值接口无法表达多通道、时间路径、关系扩展和集合召回语义。复用会让同一抽象承担两种不同决策。

### 7. 以加法方式扩展存储接口

Repository 新增批量、分层、时间、历史、关系、未同步和状态复核等有限只读方法。

现有 `MemoryIndex.query(...)` 保持不变，新增 Recall 专用 `query_candidates(...)`，由适配器封装 Chroma filter。

**理由：** Repository 和 Index 影响现有 Write、Service 和维护流程。加法扩展降低回归风险并保留现有契约。

### 8. 资格过滤与效用评分严格分离

用户、授权、有效性、显式时间/类型和数据完整性是硬过滤。语义、任务贡献、时间适配、Scope、可信度、命中稳健性和 importance 是软评分。

评分采用：

1. 全候选确定性基础分；
2. 有限前部候选一次 LLM 批量复核；
3. 透明分量组合。

LLM 不直接输出最终总分，importance 贡献有上限，候选间冗余留给集合选择。

**理由：** 硬边界不能被模型或高相似度覆盖；单条价值和集合价值需要不同语义。

### 9. 证据解析优先使用持久关系和结构化时间

解析优先级：

```text
持久化关系
→ 有效/事件时间
→ EventFrame 锚点
→ 有限语义判断
→ 保持独立或未解决冲突
```

`SUPERSEDES` 表示状态演化，`CORRECTS` 表示错误修正。事件矛盾判断前先判断事件身份。事件身份未知时保持独立，不创建持久关系或 DEFER。

未解决的关键冲突组成原子证据组并保留双方；Recall 不要求用户澄清。

**理由：** Recall 是查询视角，不能在证据不足时改变持久知识状态。

### 10. 以 EvidenceGroup 做集合选择

Set Selector 使用可解释的边际价值贪心策略，考虑直接效用、需求覆盖、互补性、必要关系、冗余和 Token 成本。

冲突组不可拆分；演化链保留目标时点、当前状态和关键变化；通道不设置最终结果强制配额。

**理由：** Top-K 无法处理重复、关系完整性和冲突对称性。

### 11. 上下文忠实渲染，不重新裁决

Assembler 从选中证据生成稳定分区：

- 当前相关记忆；
- 相关历史与变化；
- 支持信息；
- 未解决冲突或不确定性。

每项保留证据 ID、时间、角色和来源。首期通过选择、片段和紧凑格式控制预算，不依赖生成式摘要。

**理由：** 组装阶段必须可复现，不能重新引入幻觉或改变已经完成的证据裁决。

### 12. 充分性与执行状态分离

充分性使用 `SUFFICIENT / PARTIAL / CONFLICTED / EMPTY`，执行状态使用 `COMPLETE / DEGRADED`。

可恢复故障使用稳定降级代码。SQLite、授权验证和输出契约故障不可降级。

**理由：** “没有证据”和“系统没能完整寻找证据”必须区分。

### 13. 使用逻辑快照和最终复核

候选回查后形成不可变执行快照，不在 LLM/索引调用期间持有 SQLite 长事务。组装前对最终选中记录做轻量复核；发现漂移时整组移除并最多重选一次。

**理由：** 同时控制长事务风险和并发写入导致的陈旧当前事实。

### 14. Recall 领域包与编排层分离

新增建议结构：

```text
recall/
  models.py
  intent.py
  planning.py
  retrieval.py
  filtering.py
  scoring.py
  evidence.py
  selection.py
  assembly.py
  prompts.py
  errors.py

pipeline/recall.py
```

使用 `evidence.py` 避免和已有写入 `resolution/` 包混淆。Service 和 CLI 只依赖公共 Pipeline/API。

### 15. 使用 Fake 驱动默认测试

单元测试按阶段组织；集成测试使用真实 SQLite 和可控 Fake Embedder、Fake Index、Fake LLM。真实远程模型测试不进入默认套件。

**理由：** Recall 包含多个外部不稳定点，默认测试必须快速、确定且能独立注入每种失败。

## Risks / Trade-offs

- **[Repository 和 Index 影响面较大]** → 所有扩展采用加法接口；修改前执行 GitNexus impact；完成后运行 Write 全量回归。
- **[同一用户跨 Agent 共享可能过宽]** → 首期允许调用方关闭 `user_shared`；后续通过持久 visibility 模型解决，不用评分掩盖授权问题。
- **[LLM 调用增加延迟]** → 限制为一次意图、一次批量评分和必要的一次关系判断，并统一受全局截止时间约束。
- **[候选关系比较可能平方增长]** → 先按类型、主体、时间和语义邻近分桶，只比较有限高价值候选；关系扩展一跳。
- **[SQLite 回退可能退化为扫描]** → Repository 查询强制用户/Scope/类型/时间条件和数量上限，禁止 Recall 层无界扫描。
- **[逻辑快照仍可能遇到并发漂移]** → 输出前复核，最多重选一次，持续漂移时明确失败。
- **[阶段模型数量增加认知成本]** → 外部只暴露 Request/Result；内部模型按阶段放入一个 Recall 包，并保持不可变和可序列化。
- **[生成式意图可能错误缩小范围]** → 区分显式约束与推断；推断只影响软偏好；失败时回退原始查询。

## Migration Plan

1. 新增 Recall 领域模型、异常和阶段协议，不连接现有运行时。
2. 以加法方式扩展 Repository 和向量索引，运行现有 Write 回归。
3. 按阶段实现 Intent/Planner、Retrieval/Filter、Scoring/Evidence、Selection/Assembly。
4. 组装 `MemoryRecallPipeline`，使用 Fake 依赖完成完整集成测试。
5. 将 Pipeline 作为可选依赖注入 `MemoryService`，增加 Recall 专属配置校验。
6. 增加 CLI `recall` 和 JSON/诊断输出。
7. 运行 Recall、Write、架构及一致性测试，确认无同级项目依赖。
8. 在默认 Runtime 启用 Recall。

不需要迁移既有记忆数据。现有 SQLite 和 Chroma 内容继续使用；新增只读查询能够处理索引陈旧和未同步记录。

回滚时：

- 停止注入 Recall Pipeline 并移除 CLI 暴露；
- 保留加法式只读 Repository/Index 方法不会改变 Write 行为；
- 如需代码回退，不涉及持久数据逆向迁移，因为 Recall 不写入新业务状态。

## Open Questions

当前没有阻塞提案实施的开放问题。以下参数在实现时通过配置和测试夹具确定默认值，不改变本设计语义：

- 各通道和路径的默认候选额度；
- 评分分量初始权重；
- LLM 批量复核上限；
- 默认证据数和 Token 硬上限；
- 全局 Recall 超时及阶段子预算。
