## Why

`agents_memory` 已具备独立的记忆写入能力，但尚未形成从当前任务意图到可信证据上下文的完整召回闭环。现在需要把已经确认的 Recall 知识模型和实施设计转化为可执行规格，使记忆能够在严格用户边界、时间语义、冲突语义和预算约束下被稳定使用，而不是停留在向量相似度 Top-K。

## What Changes

- 新增由调用方显式触发的 `MemoryService.recall(...)`，由 Recall 内部从当前查询和有限消息窗口构建召回意图。
- 新增七阶段 Recall 管线：意图构建、分层多路径候选生成、资格过滤、单条效用评分、时间/冲突/关系证据解析、集合选择和上下文组装。
- 在同一用户边界内支持当前会话、当前 Agent 历史会话和用户级共享三层召回，并保留证据作用域来源。
- 新增结构化 `RecallResult`，同时输出可追溯证据、充分性、执行状态、降级信息和可注入模型的上下文文本。
- 扩展 SQLite Repository 的有限只读查询及最终状态复核能力，并以加法方式扩展 Chroma 的 Recall 候选查询，保持现有 Write 查询语义不变。
- 新增对索引陈旧、索引遗漏、LLM/Embedding/Chroma 局部故障和并发状态变化的明确降级或失败语义。
- 新增 CLI `recall` 命令、JSON/诊断输出及 Recall 专属配置校验。
- 新增 Recall 单元、集成和架构测试，并保持 `agents_memory` 与其他同级子项目无代码、配置及存储依赖。

## Capabilities

### New Capabilities

- `memory-recall-pipeline`: 覆盖显式 Recall API、七阶段召回、分层多路径检索、资格与效用判断、时序和冲突证据解析、集合选择、上下文组装、降级一致性、CLI 及验证要求。

### Modified Capabilities

无。

## Impact

- 新增 `agents_memory/src/agents_memory/recall/` 领域包和 `pipeline/recall.py` 编排层。
- 扩展 `MemoryService`、CLI、Settings、SQLite Repository 和 Chroma 适配器；现有 Write API 和查询契约保持兼容。
- 新增 Recall 领域模型、异常、Prompt、配置项和测试目录。
- 默认复用 `agents_memory` 自己的 GLM-4.7-Flash、Embedding、SQLite 和 Chroma 运行时配置。
- 不新增对 `agents_rag` 或其他同级子项目的运行时依赖。
- 不包含自动触发、HTTP API、Recall 写回、Reflection 调度、安全专项和完整评测专项。
