## ADDED Requirements

### Requirement: 独立子项目
系统 SHALL 将 `agents_memory` 作为可独立安装、运行和测试的 Python 子项目，并 SHALL NOT 导入、链接或在运行时依赖当前目录下任何兄弟子项目的代码、配置或存储。

#### Scenario: 独立安装和测试
- **WHEN** 开发者仅在 `agents_memory` 目录安装该项目及其声明的第三方依赖
- **THEN** 写入管线和默认测试 SHALL 可运行，且无需安装 `agents_rag` 或其他兄弟子项目

#### Scenario: 独立运行存储
- **WHEN** 用户运行 `agents-memory` CLI
- **THEN** 系统 SHALL 只读取 `agents_memory` 自己的配置并写入自己的存储目录

### Requirement: 有来源的事实抽取
系统 SHALL 从输入消息中抽取零到多条独立、自包含、可复用的 fact 或 event，并 SHALL 为每条候选记录 importance、confidence 和支撑消息来源。

#### Scenario: 抽取用户明确事实
- **WHEN** 用户明确表达一个可复用偏好或稳定事实
- **THEN** 系统 SHALL 生成带 `user_explicit` 来源和原消息 ID 的候选记忆

#### Scenario: 不强化不确定表达
- **WHEN** 用户表达“可能”“考虑”或“暂时”等不确定信息
- **THEN** 系统 SHALL 保留该不确定性，且 SHALL NOT 将其改写为已经确定的事实

#### Scenario: 不写入 Agent 推断
- **WHEN** assistant 提出建议或推断，而用户未明确确认且工具也未验证
- **THEN** 系统 SHALL NOT 把该建议或推断归属为用户事实

#### Scenario: 没有可写事实
- **WHEN** 输入仅包含寒暄、过程噪音或不可复用信息
- **THEN** 系统 SHALL 返回成功的零候选结果且 SHALL NOT 创建记忆

### Requirement: 候选质量与批内去重
系统 SHALL 在历史查重前校验候选字段、来源和粒度，并 SHALL 处理同一请求中的完全重复与语义重叠候选。

#### Scenario: 完全文本重复
- **WHEN** 同批候选经安全规范化后内容、类型和作用域完全相同
- **THEN** 系统 SHALL 只保留一个候选进入后续流程

#### Scenario: 空库中的近义候选
- **WHEN** 历史库为空且同一批候选以不同表述表达同一事实
- **THEN** 后序候选 SHALL 能看到前序拟定结果，系统 SHALL NOT 为每个表述都创建 active 记忆

#### Scenario: 保留关键语义
- **WHEN** 候选包含否定、时间或不确定性词语
- **THEN** 规则规范化 SHALL NOT 删除或改变这些词语的语义

### Requirement: 精确作用域隔离
系统 SHALL 使用 `(user_id, agent_id, session_id, type)` 作为写入查重和关系判断的精确作用域，并 MUST 将 `user_id` 作为强制隐私边界。

#### Scenario: 不同用户内容相同
- **WHEN** 两个不同 user_id 写入相同内容
- **THEN** 两条记忆 SHALL 彼此不可见且 SHALL NOT 被判定为重复

#### Scenario: 不同记忆类型主题相同
- **WHEN** 同一用户的 fact 与 event 语义主题相同
- **THEN** 系统 SHALL NOT 在写入查重阶段跨类型合并它们

#### Scenario: 空作用域值
- **WHEN** agent_id 或 session_id 为空
- **THEN** 系统 SHALL 将空值视为精确作用域值，且 SHALL NOT 隐式继承其他层级的记忆

### Requirement: 语义候选查找
系统 SHALL 对合格候选生成 embedding，并 SHALL 在 Chroma 中按精确作用域、类型和 active 状态查找 top-k 同主题历史；向量相似度 SHALL 只用于发现候选关系。

#### Scenario: 相似度高但关系未知
- **WHEN** Chroma 返回高相似历史
- **THEN** 系统 SHALL 将其交给关系判断，且 SHALL NOT 仅凭相似度直接决定 NOOP 或 UPDATE

#### Scenario: SQLite 回源校验
- **WHEN** Chroma 返回的 memory_id 在 SQLite 中不存在、作用域不匹配或不再 active
- **THEN** 系统 SHALL 忽略该结果且 SHALL NOT 将其用于动作决策

#### Scenario: 查重索引不可用
- **WHEN** Chroma 在历史查找阶段不可用
- **THEN** 系统 SHALL 以可重试错误终止请求，且 SHALL NOT 绕过查重直接 ADD

### Requirement: 四类语义关系
系统 SHALL 对每个候选与其 top-k 历史整体判断 duplicate、supplement、contradict、correct 或 none 关系，并 SHALL 校验关系输出只引用本次输入中的历史 ID。

#### Scenario: 重复关系
- **WHEN** 候选与 active 历史表达同一事实且不包含新增信息
- **THEN** 系统 SHALL 判定为 duplicate

#### Scenario: 补充关系
- **WHEN** 候选与历史围绕相似主题但提供可并存的正交信息
- **THEN** 系统 SHALL 判定为 supplement

#### Scenario: 状态变化
- **WHEN** 候选表示同维度的当前事实已从旧值变化为新值
- **THEN** 系统 SHALL 判定为 contradict

#### Scenario: 明确纠错
- **WHEN** 用户明确指出既有记忆记录错误
- **THEN** 系统 SHALL 判定为 correct

#### Scenario: 非法关系输出
- **WHEN** 关系结果包含未知关系、未知历史 ID 或缺失必需字段
- **THEN** 系统 SHALL 拒绝该结果，并在有界修复仍失败后终止本次写入

### Requirement: 确定性写入动作
系统 SHALL 通过确定性 DecisionEngine 将已校验的关系集合映射为 ADD、UPDATE 或 NOOP，并 SHALL 在无法形成安全唯一动作时拒绝自动写入。

#### Scenario: 新事实新增
- **WHEN** 候选没有相似历史，或所有关系均为 supplement/none
- **THEN** 系统 SHALL 执行 ADD 并创建 active 记忆

#### Scenario: 当前事实重复
- **WHEN** 候选只与当前 active 记忆形成 duplicate 且没有新增信息
- **THEN** 系统 SHALL 执行 NOOP 且 SHALL NOT 创建新记忆

#### Scenario: 事实变化更新
- **WHEN** 候选与 active fact 形成 contradict
- **THEN** 系统 SHALL 执行 UPDATE，将旧记录标记为 superseded、创建 active 新记录并建立 supersedes 关系

#### Scenario: 错误事实纠正
- **WHEN** 候选与既有记录形成 correct
- **THEN** 系统 SHALL 执行 UPDATE，将旧记录标记为 retracted、创建 active 新记录并建立 corrects 关系

#### Scenario: 新事件不覆盖旧事件
- **WHEN** 一个新 event 表示在不同时间发生的后续经历且未明确纠正旧事件
- **THEN** 系统 SHALL 执行 ADD，且 SHALL NOT 把旧 event 标记为 superseded

#### Scenario: 混合关系歧义
- **WHEN** 一个候选同时包含无法由动作矩阵安全化解的补充和冲突关系
- **THEN** 系统 SHALL 拒绝整批动作计划且 SHALL NOT 猜测写入

### Requirement: 当前有效性与历史追溯
系统 SHALL 持久化 active、superseded 和 retracted 有效性，并 SHALL 保留事实变化和纠错的来源及关系历史。

#### Scenario: 默认当前视图
- **WHEN** 调用方列出记忆且未请求历史
- **THEN** 系统 SHALL 只返回 active 记忆

#### Scenario: 查看历史链
- **WHEN** 调用方查看一条发生过 UPDATE 的记忆
- **THEN** 系统 SHALL 能返回其来源、旧记录状态和 supersedes/corrects 关系

#### Scenario: 历史记录不参与默认查重
- **WHEN** 旧记录已经 superseded 或 retracted
- **THEN** 系统 SHALL NOT 将其作为默认 active 历史直接触发 NOOP

### Requirement: SQLite 真相源与事务
系统 SHALL 以 SQLite 作为唯一真相源，并 SHALL 在单个事务中提交同一请求的记忆、来源、关系、幂等结果和待执行索引操作。

#### Scenario: 动作计划成功提交
- **WHEN** 整批动作计划校验通过
- **THEN** 系统 SHALL 在一个 SQLite 事务中提交全部业务变更和 pending 索引操作

#### Scenario: SQLite 失败
- **WHEN** SQLite 事务发生错误
- **THEN** 系统 SHALL 回滚整批业务变更且 SHALL NOT 操作 Chroma

#### Scenario: 落库前候选失败
- **WHEN** 任一候选在抽取、关系解析或动作计划阶段产生不可接受错误
- **THEN** 系统 SHALL NOT 持久化该请求中的任何候选动作

### Requirement: Chroma 派生索引
系统 SHALL 将 Chroma 作为可从 SQLite 重建的派生语义索引，并 SHALL 使用 memory_id 作为幂等 upsert/delete 标识。

#### Scenario: 正常同步
- **WHEN** SQLite 事务提交成功
- **THEN** 系统 SHALL 同步执行对应的 Chroma upsert/delete 并将索引操作标记为 synced

#### Scenario: 索引同步失败
- **WHEN** SQLite 已提交但 Chroma 同步失败
- **THEN** 系统 SHALL 保留业务真相，将索引操作标记为 failed，并返回 retryable 状态

#### Scenario: 修复失败操作
- **WHEN** 用户运行索引 repair
- **THEN** 系统 SHALL 重放 pending/failed 操作并通过 memory_id 幂等收敛

#### Scenario: 全量重建
- **WHEN** 用户运行索引 rebuild
- **THEN** 系统 SHALL 以 SQLite 当前 active 记忆为准重建 Chroma collection

#### Scenario: 模型维度变化
- **WHEN** 配置的 embedding 模型或维度与现有 collection 不兼容
- **THEN** 系统 SHALL 拒绝混写并要求重建索引

### Requirement: 请求幂等
系统 SHALL 使用 request_id 与输入摘要定义请求级幂等，并 SHALL 保存可复用的 WriteReport 结果快照。

#### Scenario: 重复成功请求
- **WHEN** 相同 request_id 和相同输入再次提交，且原请求已完成同步
- **THEN** 系统 SHALL 直接返回原 WriteReport，且 SHALL NOT 重复调用模型或创建记忆

#### Scenario: 重试未同步请求
- **WHEN** 相同 request_id 和相同输入再次提交，且 SQLite 已提交但索引未完成
- **THEN** 系统 SHALL 只重试未完成的索引操作

#### Scenario: 提交前失败后重试
- **WHEN** 原请求在 SQLite 提交前失败
- **THEN** 系统 SHALL 允许相同 request_id 和输入重新执行完整管线

#### Scenario: 幂等键误用
- **WHEN** 相同 request_id 携带不同输入摘要
- **THEN** 系统 SHALL 拒绝请求且 SHALL NOT 修改已有结果

### Requirement: 显式删除
系统 SHALL 在校验用户归属后删除记忆真相和派生索引，并 SHALL 使索引删除失败可以重试。

#### Scenario: 删除自己的记忆
- **WHEN** 调用方使用匹配的 user_id 删除 memory_id
- **THEN** 系统 SHALL 删除该记忆正文及相关来源/关系，并同步删除 Chroma 记录

#### Scenario: 跨用户删除
- **WHEN** 调用方 user_id 与目标记忆不匹配
- **THEN** 系统 SHALL 拒绝删除且 SHALL NOT 泄露目标记忆内容

#### Scenario: 删除索引失败
- **WHEN** SQLite 删除已提交但 Chroma 删除失败
- **THEN** 系统 SHALL 保存可重试索引操作，且 rebuild 后被删内容 SHALL NOT 恢复

### Requirement: 可观察 API 与 CLI
系统 SHALL 提供 MemoryWritePipeline/MemoryService Python API 和 `write/list/show/delete/sync` CLI，并 SHALL 通过 WriteReport 解释每个候选的处理与存储结果。

#### Scenario: 写入报告
- **WHEN** 用户通过 API 或 CLI 完成写入
- **THEN** WriteReport SHALL 包含候选处理、历史命中、关系、动作、记忆 ID、SQLite 状态、Chroma 状态和可重试分类

#### Scenario: JSON 自动化输出
- **WHEN** 用户为 CLI 指定 JSON 输出
- **THEN** 系统 SHALL 返回结构化且可机器解析的 WriteReport

#### Scenario: 无密钥的本地查询
- **WHEN** 未配置模型 API key 且用户执行只读取 SQLite 的 list 或 show
- **THEN** 系统 SHALL 允许命令运行

#### Scenario: 缺少密钥的写入
- **WHEN** 未配置模型 API key 且用户执行 write
- **THEN** 系统 SHALL 在付费调用前以明确配置错误终止

### Requirement: 可测试性与质量门槛
系统 SHALL 支持通过依赖注入替换 LLM、Embedder、Repository 和 Index，并 SHALL 使默认测试不访问真实网络。

#### Scenario: Fake 全链路测试
- **WHEN** 测试注入确定性的 Fake Extractor、Embedder 和 Resolver
- **THEN** 系统 SHALL 可验证 ADD、UPDATE、NOOP、隔离、幂等和修复行为

#### Scenario: 默认质量检查
- **WHEN** 开发者运行项目标准验证命令
- **THEN** pytest 和 ruff SHALL 通过，测试覆盖率 SHALL 不低于 80%

#### Scenario: 真实模型验证隔离
- **WHEN** 未显式启用真实 API 端到端测试
- **THEN** 默认测试套件 SHALL NOT 读取真实密钥或发起网络调用
