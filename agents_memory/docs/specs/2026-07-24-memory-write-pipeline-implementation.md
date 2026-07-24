# agents_memory 记忆写入管线 · 实施设计

| 项 | 值 |
|----|----|
| 日期 | 2026-07-24 |
| 状态 | 已批准，待提案与实现 |
| 范围 | Memory 写入管线首期核心闭环 |
| 交付形态 | 独立 Python 包 + CLI 演示 |
| 运行环境 | conda 环境 `agents_glm`（Python 3.12.13） |
| 上游文档 | [记忆写入管线知识笔记](../knowledge/memory-write-pipeline.md) |
| 工程参考 | `agents_rag` 的项目结构、配置、抽象接口、CLI 与测试方法 |

---

## 0. 文档定位

本文把写入管线知识笔记中的核心认知转化为 `agents_memory` 首期可执行的工程边界，回答：

- 本轮具体交付什么，做到哪里停止；
- 写入过程由哪些组件负责，各组件如何协作；
- 数据、状态、一致性和失败恢复如何定义；
- 如何验证实现真正覆盖 ADD / UPDATE / NOOP 等核心语义。

知识原理与方案取舍见[记忆写入管线知识笔记](../knowledge/memory-write-pipeline.md)，本文不重复展开。

`agents_rag` 只作为工程组织方式的参考。`agents_memory` 是独立子项目，必须独立声明第三方依赖、独立配置、独立运行和测试，禁止导入或链接当前目录下其他子项目的代码、数据目录或运行时对象。

---

## 1. 首期目标与范围

### 1.1 核心目标

实现一条可独立运行、可观察、可重试的记忆写入主线：

```text
交互消息
  → 事实抽取
  → 候选质量处理与批内查重
  → 历史记忆查找
  → 语义关系判断
  → ADD / UPDATE / NOOP 决策
  → SQLite 真相源写入
  → Chroma 语义索引同步
  → WriteReport
```

首期不是简单的“抽取后存入向量库”，而是验证以下完整语义：

1. 只将有来源、可复用且具备足够确定性的事实写入长期记忆；
2. 同时处理“候选与历史”和“同批候选之间”的重复；
3. 区分 duplicate / supplement / contradict / correct；
4. 以 ADD / UPDATE / NOOP 表达业务动作；
5. UPDATE 保留历史，并明确当前有效性，而不是静默覆盖；
6. SQLite 与 Chroma 部分失败后可以通过幂等重试收敛；
7. CLI 能展示各候选的判断依据和最终结果。

### 1.2 必须跑通的场景

| 场景 | 预期结果 |
|------|----------|
| 首次写入独立事实 | ADD，新记忆为 active |
| 相同事实换一种表达 | NOOP，不产生重复记录 |
| 同主题的正交补充 | ADD，旧记忆保持 active |
| 用户偏好、住址等当前事实变化 | UPDATE，旧记录 superseded，新记录 active |
| 用户明确纠正历史错误 | UPDATE，旧记录 retracted，新记录 active |
| 同一次抽取出现重复候选 | 批内去重，只形成一个有效动作 |
| 同一 `request_id` 重复提交 | 返回既有结果或继续未完成的索引同步，不重复推理和写库 |
| SQLite 成功、Chroma 失败 | 真相记录保留，报告 retryable，后续可修复 |
| 不同用户存在相同内容 | 严格隔离，互不参与查重和关系判断 |
| 用户显式删除记忆 | SQLite 与 Chroma 均删除或进入可重放的修复状态 |

### 1.3 首期不做

- 召回、重排和 prompt 注入管线；
- recency / importance / access_frequency 三信号召回评分；
- 遗忘、合并、反思等维护管线；
- FastAPI、Web UI、分布式部署；
- 消息队列、后台 worker、异步 outbox 消费；
- 图谱、实体本体、复杂时间推理；
- 跨项目代码复用；
- 生产级多租户权限系统。

这些能力不应侵入首期写入核心，但数据结构需保留自然演进空间。

---

## 2. 架构方案

### 2.1 选定路线：模块化混合管线

采用“LLM 负责语义理解，确定性逻辑负责边界和状态转换”的混合方案：

```text
Public API / CLI
        │
        ▼
MemoryWritePipeline
        ├── FactExtractor          # LLM：从消息抽取结构化候选
        ├── CandidateProcessor     # 规则：校验、规范化、批内去重
        ├── ContextLookup          # Embedding + Chroma：发现同主题历史
        ├── RelationResolver       # LLM：判断四类语义关系
        ├── DecisionEngine         # 规则：关系集合映射为动作计划
        └── StorageCoordinator     # SQLite 提交 + Chroma 同步/修复
                   ├── MemoryRepository（SQLite，真相源）
                   └── MemoryIndex（Chroma，派生索引）
```

职责边界：

- LLM 不直接生成数据库操作，也不能指定任意记录 ID；
- 向量相似度只负责发现关系候选，不能直接决定 NOOP 或 UPDATE；
- DecisionEngine 只消费合法关系结果，以确定性规则产生动作；
- SQLite 决定事实状态，Chroma 只提供语义候选；
- Pipeline 只负责编排，不承载组件内部规则。

### 2.2 参考 `agents_rag` 的部分

| 参考点 | 在 `agents_memory` 中的采用方式 |
|--------|--------------------------------|
| 独立 `pyproject.toml` + `src/` 布局 | 建立独立可安装包与 CLI 入口 |
| Pydantic frozen 数据模型 | 将跨组件契约定义为不可变模型 |
| pydantic-settings | 集中读取 `.env` 与默认配置 |
| 抽象接口 + 具体实现 | LLM、Embedder、Repository、Index 可替换 |
| OpenAI 兼容协议 | 抽取、关系判断和 embedding 使用统一客户端范式 |
| tenacity 重试边界 | 只重试网络、限流和服务端临时错误 |
| Fake 驱动单元测试 | 测试不依赖真实模型与外部网络 |
| Typer + Rich CLI | 提供可观察的本地演示和管理入口 |
| 覆盖率门槛 | 单元和集成测试总覆盖率不低于 80% |

明确不采用：

- 不导入 `agents_rag` 的配置、模型、客户端、Chroma 封装或测试工具；
- 不共用 `agents_rag/storage`；
- 不读取其 `.env`；
- 不要求先安装或启动 `agents_rag`。

### 2.3 首期目录结构

```text
agents_memory/
├── README.md
├── pyproject.toml
├── .env.example
├── docs/
│   ├── knowledge/
│   └── specs/
├── storage/                         # 运行产物，gitignore
│   ├── memory.sqlite
│   └── chroma/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── src/agents_memory/
    ├── __init__.py
    ├── config.py                    # 配置与启动校验
    ├── models.py                    # 跨组件领域模型
    ├── cli.py                       # write/list/show/delete/sync
    ├── extraction/
    │   ├── base.py                  # FactExtractor 协议
    │   ├── llm.py                   # OpenAI 兼容结构化抽取
    │   └── prompts.py
    ├── processing/
    │   ├── candidate.py             # 校验、规范化、批内去重
    │   └── decision.py              # 关系集合 → 动作计划
    ├── embedding/
    │   ├── base.py                  # Embedder 协议
    │   └── openai.py
    ├── retrieval/
    │   └── lookup.py                # scope 过滤与 SQLite 回源校验
    ├── resolution/
    │   ├── base.py                  # RelationResolver 协议
    │   ├── llm.py
    │   └── prompts.py
    ├── storage/
    │   ├── repository.py            # SQLite 真相源
    │   ├── vector.py                # Chroma 派生索引
    │   └── coordinator.py           # 同步与修复状态机
    └── pipeline/
        └── write.py                 # 写入管线编排
```

---

## 3. 核心数据契约

### 3.1 输入与作用域

写入入口接收：

```text
messages: 一组带 message_id、role、content 的交互消息
user_id: 必填
agent_id: 可选
session_id: 可选
request_id: 必填或由调用方稳定生成的幂等键
```

首期写入查重采用精确 scope：

```text
(user_id, agent_id, session_id, type)
```

- `user_id` 是强制隐私边界；
- `agent_id`、`session_id` 为空也是确定值，不代表自动继承；
- 写入阶段不把用户级、Agent 级、Session 级记忆混在一起查重；
- 后续召回管线可以按策略聚合多个层级，但不改变本轮写入边界。

### 3.2 CandidateMemory

事实抽取器输出的候选至少包含：

```text
content             独立、自包含的事实文本
type                fact | event
importance          1..10
confidence          0..1
source_message_ids  支撑该事实的消息 ID
source_kind         user_explicit | user_confirmed | tool_verified
```

约束：

- 不允许将 assistant 建议、推测或旧记忆本身作为用户事实来源；
- 工具结果只有在语义上与当前用户/任务主体一致时才可作为证据；
- “可能、考虑、暂时”等不确定性必须保留，不能强化为既定事实；
- 零候选是合法结果，表示本轮没有值得写入的记忆。

### 3.3 MemoryRecord

SQLite 中的核心记录：

```text
id
user_id / agent_id / session_id
type                         fact | event
content
importance                  1..10
confidence                  0..1
validity                    active | superseded | retracted
created_at / updated_at
valid_from                  可选
metadata                    扩展字段
```

`validity` 与“是否保留历史”分离：

- `active`：代表当前可参与默认查重与召回；
- `superseded`：曾经成立，但已被新事实替代；
- `retracted`：原记录被明确纠错，不应再作为事实使用。

### 3.4 来源与关系

来源记录 `MemorySource`：

```text
memory_id
message_id
role
source_kind
excerpt
created_at
```

持久关系 `MemoryRelation` 只记录具有历史意义的变化：

```text
from_memory_id
to_memory_id
relation                   supersedes | corrects
created_at
```

`duplicate` 和 `supplement` 是写入判断过程中的关系，不需要形成永久边。

### 3.5 处理结果

`WriteReport` 至少包含：

- `request_id` 与整体状态；
- 原始抽取候选数量、过滤数量；
- 每个候选的批内处理结果；
- 检索到的历史 ID 与相似度；
- 关系判断摘要；
- 最终动作 ADD / UPDATE / NOOP；
- 新旧记忆 ID；
- SQLite 提交结果；
- Chroma 同步状态；
- 是否可重试及错误分类。

报告既是 CLI 的可观察输出，也是幂等请求复用的结果快照。

---

## 4. 写入时序

### 4.1 主流程

```text
1. 校验输入与 scope
2. 根据 request_id 查询 write_requests
3. LLM 抽取 0..N 个 CandidateMemory
4. 校验来源、粒度、字段范围并做确定性规范化
5. 处理批内精确重复与语义重叠
6. 批量生成候选 embedding
7. 按精确 scope + type 从 Chroma 查询 top_k
8. 用 SQLite 回源并验证记录仍为 active
9. 对每个候选调用一次 RelationResolver，整体判断其与 top_k 的关系
10. DecisionEngine 生成整批动作计划
11. SQLite 单事务写入记忆、来源、关系、请求结果与索引操作
12. 事务提交后同步 Chroma
13. 更新索引操作状态并返回 WriteReport
```

关键约束：

- RelationResolver 每个候选调用一次，输入其全部 top-k，而不是对每一对记录单独调用；
- 后续候选必须看到本批前序候选的拟定结果，避免空库首次写入时批量重复 ADD；
- 所有候选的动作计划先验证完成，再开始 SQLite 写入；
- SQLite 写入以一个请求为事务边界，不产生半套业务状态；
- Chroma 不在 SQLite 事务内，失败通过持久化索引操作收敛。

### 4.2 关系与动作矩阵

| 关系结果 | 动作 | 状态变化 |
|----------|------|----------|
| 无相似历史 | ADD | 新记录 active |
| 全部为 supplement / none | ADD | 新旧均 active |
| 与当前 active 事实 duplicate，且无新增信息 | NOOP | 不创建记忆 |
| contradict | UPDATE | 旧记录 superseded，新记录 active，建立 supersedes |
| correct | UPDATE | 旧记录 retracted，新记录 active，建立 corrects |

补充规则：

- UPDATE 是业务动作，不等于原地覆盖；
- fact 可因当前状态变化而 UPDATE；
- event 通常按时间序列 ADD，只有用户明确指出历史事件记错时才 correct；
- 若一个候选同时与多条 active 记录形成无法确定的混合关系，拒绝自动写入并报告歧义；
- 不能采用“任一 duplicate 即 NOOP”的简单优先级；
- 候选若同时包含补充和矛盾信息，应优先视为粒度异常，而不是猜测动作。

### 4.3 批内处理

批内处理分两层：

1. 确定性层：空白折叠、大小写等安全归一化、完全相同文本去重；
2. 语义层：按候选顺序让后续项看到前序拟定的新增/更新结果。

不在规则层进行激进文本重写，避免把“不喜欢 Java”与“喜欢 Java”等关键否定信息规范化掉。

---

## 5. 存储与一致性

### 5.1 SQLite：唯一真相源

首期逻辑表：

| 表 | 责任 |
|----|------|
| `memories` | 记忆正文、scope、类型、重要性、有效性 |
| `memory_sources` | 原始消息证据与归属 |
| `memory_relations` | supersedes / corrects 历史关系 |
| `write_requests` | 请求幂等状态与 WriteReport 快照 |
| `index_operations` | Chroma upsert/delete 的待同步、成功、失败状态 |

建议索引：

- `(user_id, agent_id, session_id, type, validity)`；
- `(user_id, created_at)`；
- `write_requests(request_id)` 唯一索引；
- `index_operations(status, updated_at)`。

### 5.2 Chroma：可重建的派生索引

每条向量记录：

```text
id        = memory_id
document  = content
embedding = 当前 embedding 模型产出的向量
metadata  = user_id / agent_id / session_id / type / validity / created_at
```

约束：

- collection 固定使用 cosine 空间；
- collection 与 embedding 模型名、维度绑定，配置变化需重建；
- 默认查重只检索 active；
- Chroma 返回的 ID 必须回 SQLite 校验 scope、类型和有效性；
- SQLite 中不存在或非 active 的 Chroma 结果一律忽略；
- 索引可从 SQLite 全量重建，不承担不可恢复的真相。

### 5.3 同步顺序

采用“SQLite 先提交，Chroma 后同步”的同步协调：

```text
SQLite transaction:
  写业务状态
  写 write_request
  写 index_operations(pending)
commit
  ↓
同步 Chroma upsert/delete
  ↓
index_operations → synced 或 failed
```

这不是异步消息系统。`index_operations` 首期只是本地修复日志：

- 正常请求在提交后立即同步；
- 同一 `request_id` 重试时优先继续未完成同步；
- `sync repair` 可显式重放 failed/pending 操作；
- `sync rebuild` 可按 SQLite 当前状态重建整个 collection。

### 5.4 幂等规则

| 已有请求状态 | 再次提交同一 request_id |
|--------------|--------------------------|
| 不存在 | 正常执行 |
| 已完成且索引已同步 | 直接返回原 WriteReport，不再调用 LLM |
| SQLite 已提交、索引未完成 | 只重放索引操作 |
| SQLite 提交前失败 | 允许重新执行抽取与判断 |
| 相同 request_id 但输入摘要不同 | 拒绝，防止幂等键误用 |

### 5.5 删除

显式删除必须：

1. 校验目标属于调用方 `user_id`；
2. 在 SQLite 中删除相关来源、关系和记忆，或按产品策略留下最小审计信息；
3. 记录 Chroma delete 操作；
4. 同步删除向量；
5. 失败时进入可重试修复状态。

首期对用户“忘记这条记忆”的语义采用物理删除，不让被删正文继续出现在默认查询、历史列表或重建后的索引中。

---

## 6. 错误处理与恢复边界

### 6.1 失败分类

| 失败 | 处理 |
|------|------|
| 输入、scope、字段范围不合法 | 调用任何付费服务前失败，不重试 |
| 缺少 API key | 需要模型的命令 fail-fast；纯 SQLite 的 list/show/delete 仍可运行 |
| 认证失败、请求参数错误 | 不重试，返回明确配置错误 |
| 429、网络波动、服务端 5xx | 有界指数退避重试 |
| LLM 输出不符合 schema | 允许一次结构修复/重试，仍失败则终止本请求 |
| Chroma 在查重前不可用 | 关闭写入，不绕过查重直接 ADD |
| 关系集合存在歧义 | 不猜测，不写入，报告需要人工或上游澄清 |
| SQLite 事务失败 | 回滚，不操作 Chroma |
| SQLite 成功、Chroma 失败 | 保留真相与修复日志，返回 retryable |
| Chroma 部分 upsert/delete | 依靠 memory_id 幂等重放 |

### 6.2 请求原子性

- 抽取、查重、关系判断属于“生成动作计划”阶段；
- 任何候选在该阶段出现不可接受错误，整批请求在落库前失败；
- 零候选属于成功 NOOP，不是异常；
- 动作计划合法后，所有 SQLite 业务变更在单事务中提交；
- Chroma 同步允许暂时不原子，但必须最终可收敛。

首期选择请求级原子性，是为了避免同一轮对话中部分事实已写入、部分事实因语义判断失败而丢失，导致调用方难以理解和安全重试。

---

## 7. 对外 API 与 CLI

### 7.1 Python API

```text
MemoryWritePipeline.write(...) -> WriteReport

MemoryService.list_memories(...)
MemoryService.get_memory(...)
MemoryService.delete_memory(...)
MemoryService.repair_index(...)
MemoryService.rebuild_index(...)
```

Pipeline 构造时通过依赖注入接收 extractor、embedder、resolver、repository 和 index，生产环境使用真实实现，测试环境使用 Fake。

### 7.2 CLI

| 命令 | 用途 |
|------|------|
| `agents-memory write` | 从 JSON 文件或 stdin 读取消息并执行写入 |
| `agents-memory list` | 按 scope/type 列出记忆，默认只看 active |
| `agents-memory show` | 查看正文、来源、状态与历史关系 |
| `agents-memory delete` | 按 memory_id 删除并同步索引 |
| `agents-memory sync repair` | 重放 pending/failed 索引操作 |
| `agents-memory sync rebuild` | 以 SQLite 为准重建 Chroma |

`write` 输出至少展示：

- 抽取了哪些候选；
- 哪些候选被过滤或批内合并；
- 查到了哪些相关历史；
- 语义关系与最终动作；
- SQLite 与 Chroma 状态；
- 可否安全重试。

默认输出面向人阅读，提供 JSON 选项便于自动化测试与后续 API 接入。

---

## 8. 配置与依赖

### 8.1 配置

```text
LLM_API_KEY
LLM_BASE_URL
MEMORY_EXTRACT_MODEL
MEMORY_RELATION_MODEL
EMBEDDING_MODEL
EMBEDDING_DIM
EMBEDDING_MAX_BATCH
EMBEDDING_MAX_CONCURRENCY
MEMORY_LOOKUP_TOP_K
MEMORY_LOOKUP_THRESHOLD
MEMORY_STORAGE_DIR
```

默认策略：

- 抽取与关系判断可使用不同的 Flash 级模型；
- embedding 首期使用 OpenAI 兼容的 embedding-3；
- top_k 和 threshold 只是可评测的起始配置，不写死为业务真理；
- 所有路径相对 `agents_memory` 子项目解析；
- 真实 `.env` 与 `storage/` 不入库。

### 8.2 独立依赖

运行依赖建议：

- `openai`
- `chromadb`
- `pydantic`
- `pydantic-settings`
- `typer`
- `rich`
- `tenacity`

开发依赖建议：

- `pytest`
- `pytest-cov`
- `ruff`

即使与 `agents_rag` 使用相同第三方库，也必须在 `agents_memory/pyproject.toml` 中独立声明。

---

## 9. 测试策略

### 9.1 单元测试

| 测试对象 | 关键断言 |
|----------|----------|
| 领域模型 | type、importance、confidence、validity 校验 |
| 来源守卫 | 不把 assistant 推断写成用户事实，保留不确定性 |
| CandidateProcessor | 精确重复、批内重复、空候选、否定词不被错误归一 |
| ContextLookup | 精确 scope/type 过滤，Chroma 结果经 SQLite 回源 |
| RelationResolver 解析器 | 合法四关系、未知 ID、缺字段、混合歧义 |
| DecisionEngine | ADD/UPDATE/NOOP 矩阵，event 规则，一对多规则 |
| Repository | 事务、状态迁移、来源与关系、幂等键 |
| StorageCoordinator | pending/synced/failed 状态与重放 |

### 9.2 集成测试

使用临时 SQLite、临时 Chroma、确定性 Fake Embedder 和 Fake LLM，覆盖：

1. 首次 ADD；
2. 改写重复 NOOP；
3. supplement ADD；
4. contradict → superseded + active；
5. correct → retracted + active；
6. 空库批内去重；
7. 跨用户与跨 type 隔离；
8. 重复 request_id；
9. Chroma 故障后 repair；
10. 全量 rebuild；
11. 删除与索引收敛；
12. 零候选成功。

真实 API 只做显式的手工端到端验证，不进入默认测试套件，避免测试依赖网络、密钥和模型随机性。

### 9.3 质量门槛

- `pytest` 全绿；
- 覆盖率不低于 80%；
- `ruff check` 通过；
- 所有失败路径都有稳定错误分类；
- 默认测试不发起真实网络调用；
- 独立安装 `agents_memory` 时不需要安装任何兄弟子项目。

---

## 10. 实施顺序

1. 建立独立项目骨架、配置、领域模型和测试基础；
2. 实现 SQLite schema、Repository、事务与幂等请求；
3. 定义 Embedder/MemoryIndex 接口并实现 Chroma 适配；
4. 实现 FactExtractor 结构化输出与来源守卫；
5. 实现候选校验、规范化和批内处理；
6. 实现 ContextLookup 与 SQLite 回源校验；
7. 实现 RelationResolver 与结构化结果校验；
8. 实现 DecisionEngine 动作矩阵；
9. 实现 StorageCoordinator 同步、重放和重建；
10. 完成 MemoryWritePipeline 编排；
11. 完成 CLI 与人类可读/JSON 报告；
12. 补齐集成测试、真实 API 手工验证和 README。

依赖方向保持单向：

```text
models/config
  ← extraction / embedding / retrieval / resolution / storage
  ← processing
  ← pipeline
  ← cli
```

任何底层组件不得反向导入 pipeline 或 CLI。

---

## 11. 验收标准

- [ ] `agents_memory` 可独立安装、测试和运行；
- [ ] 不存在对 `agents_rag` 或其他兄弟子项目的 Python 导入和运行时依赖；
- [ ] `agents-memory write` 跑通抽取到双存储同步的完整闭环；
- [ ] fact/event、importance、confidence、来源均被持久化；
- [ ] assistant 推断不会被错误归属为用户事实；
- [ ] 候选与历史、候选与同批候选均参与去重；
- [ ] duplicate / supplement / contradict / correct 均有测试；
- [ ] ADD / UPDATE / NOOP 行为符合动作矩阵；
- [ ] UPDATE 保留历史，并正确设置 superseded/retracted 与关系；
- [ ] 精确 scope 隔离，不发生跨用户、跨 Agent、跨 Session 串扰；
- [ ] 相同 `request_id` 重试不产生重复记忆或重复模型调用；
- [ ] SQLite 成功而 Chroma 失败时可通过 repair 收敛；
- [ ] 删除同时覆盖真相源和派生索引，并可重试；
- [ ] CLI 可解释每个候选的判断与落库状态；
- [ ] 默认测试不依赖真实 API，覆盖率不低于 80%；
- [ ] 至少完成一次真实密钥的手工端到端验证。

---

## 12. 后续阶段

写入核心闭环稳定后，再分别进入：

1. 召回管线：候选生成、三信号评分、token 预算与 prompt 注入；
2. 维护管线：衰减、遗忘、合并、反思与孤儿清理；
3. 评测体系：抽取准确率、噪音率、重复率、冲突判定准确率；
4. 服务化与异步化：只有在延迟或吞吐证据明确后，再引入 API、worker 或队列。

本设计不提前承诺这些实现，只保证首期数据和组件边界不会阻断其自然演进。
