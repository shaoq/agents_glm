## Why

`agents_memory` 目前只有记忆写入、召回和维护的知识文档，尚无可运行的写入闭环。首期需要先建立一个独立、可测试且可恢复的写入管线，把交互消息转化为有来源、有作用域、有当前有效性的长期记忆，为后续召回与维护提供可信真相源。

## What Changes

- 建立独立 Python 子项目 `agents_memory`，拥有自己的依赖、配置、源码、CLI、测试和存储目录，不依赖任何兄弟子项目代码。
- 新增事实抽取、来源归属、候选校验和批内查重能力。
- 新增按用户、Agent、Session 和记忆类型隔离的语义查重。
- 新增 duplicate / supplement / contradict / correct 关系判断，以及确定性的 ADD / UPDATE / NOOP 动作决策。
- 使用 SQLite 保存完整记忆、来源、历史关系、幂等请求和索引修复状态，作为唯一真相源。
- 使用 Chroma 保存可重建的语义索引，并提供同步重试、修复、重建和显式删除能力。
- 新增 Python 写入 API 与 `write/list/show/delete/sync` CLI，输出可解释的 `WriteReport`。
- 新增不依赖真实网络的单元和集成测试，并提供真实模型手工端到端验证入口。

## Capabilities

### New Capabilities

- `memory-write-pipeline`: 定义消息到记忆的抽取、去重、关系判断、动作决策、作用域隔离、幂等写入、双存储同步、删除修复和可观察报告行为。

### Modified Capabilities

无。

## Impact

- 新增 `agents_memory/pyproject.toml`、`.env.example`、`README.md`、`src/agents_memory/`、`tests/` 和本地运行存储布局。
- 新增 `agents-memory` CLI 和 `MemoryWritePipeline` / `MemoryService` Python API。
- 新增运行依赖：OpenAI 兼容 SDK、Chroma、Pydantic、pydantic-settings、Typer、Rich、Tenacity。
- 运行时会产生独立的 SQLite 数据库和 Chroma collection；两者均位于 `agents_memory` 自己的存储目录。
- 不修改 `agents_rag` 或其他兄弟子项目的代码、接口、依赖和存储。
