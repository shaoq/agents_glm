## 1. 独立项目骨架

- [x] 1.1 创建 `agents_memory` 独立 `pyproject.toml`、`src/agents_memory` 包、CLI 入口、测试目录和 `.gitignore` 存储规则
- [x] 1.2 添加独立 `.env.example`、pydantic-settings 配置模型与启动校验
- [x] 1.3 添加架构约束测试，确认源码和依赖不引用任何兄弟子项目
- [x] 1.4 配置 pytest、覆盖率和 ruff，并验证最小包可独立安装

## 2. 领域模型与错误契约

- [x] 2.1 先编写 CandidateMemory、MemoryRecord、MemorySource、MemoryRelation 和 scope 校验测试
- [x] 2.2 实现不可变领域模型、枚举、时间字段和字段范围校验
- [x] 2.3 先编写 WriteReport、候选步骤结果和错误分类序列化测试
- [x] 2.4 实现 WriteReport、动作计划和稳定错误契约

## 3. SQLite 真相源

- [x] 3.1 先编写 schema 初始化、memories、sources、relations CRUD 和精确 scope 查询测试
- [x] 3.2 实现 SQLite 连接、schema 版本、事务边界和 Repository
- [x] 3.3 先编写 active/superseded/retracted 状态迁移及历史链测试
- [x] 3.4 实现 ADD、contradict、correct 的事务化持久化操作
- [x] 3.5 先编写 write_requests 输入摘要、结果快照和重复 request_id 测试
- [x] 3.6 实现请求幂等状态与提交前/提交后失败区分
- [x] 3.7 先编写 index_operations pending/synced/failed 状态测试
- [x] 3.8 实现可重放的索引操作日志

## 4. Embedding 与 Chroma 索引

- [x] 4.1 定义 Embedder 和 MemoryIndex 协议，并提供确定性 Fake
- [x] 4.2 先编写 OpenAI 兼容 Embedder 的批量、维度校验、重试与非重试错误测试
- [x] 4.3 实现 OpenAI 兼容 Embedder
- [x] 4.4 先编写 Chroma upsert/query/delete、metadata 过滤和模型维度绑定测试
- [x] 4.5 实现独立 Chroma collection 与 memory_id 幂等操作

## 5. 事实抽取与来源守卫

- [x] 5.1 先编写结构化抽取解析、零候选、字段越界和一次修复失败测试
- [x] 5.2 先编写 user_explicit/user_confirmed/tool_verified 来源和 assistant 推断拒绝测试
- [x] 5.3 实现 FactExtractor 协议、OpenAI 兼容 LLM 抽取器与 prompt
- [x] 5.4 实现来源归属、不确定性保留和输入消息 ID 校验

## 6. 候选处理

- [x] 6.1 先编写安全文本规范化、精确重复、空候选和否定词保留测试
- [x] 6.2 实现 CandidateProcessor 的字段校验、保守规范化和确定性批内去重
- [x] 6.3 先编写空库近义候选的顺序可见性测试
- [x] 6.4 实现批内拟定结果上下文，为后续候选查重提供可见性

## 7. 历史查找与关系判断

- [x] 7.1 先编写精确 user/agent/session/type/active 过滤和 SQLite 回源测试
- [x] 7.2 实现 ContextLookup 的批量 embedding、Chroma top-k 查询与真相校验
- [x] 7.3 先编写 RelationResolver 四类关系、none、未知 ID、缺字段和混合结果测试
- [x] 7.4 实现每候选一次整体判断的 LLM RelationResolver 与 prompt
- [x] 7.5 验证 Chroma 不可用时写入关闭且返回可重试错误

## 8. 确定性决策

- [x] 8.1 先编写无历史/补充 ADD、当前重复 NOOP、contradict/correct UPDATE 的决策矩阵测试
- [x] 8.2 先编写 event 新经历 ADD、event 明确纠错 UPDATE 和历史 duplicate 不直接 NOOP 测试
- [x] 8.3 先编写一对多混合歧义拒绝和粗粒度候选拒绝测试
- [x] 8.4 实现纯逻辑 DecisionEngine 并生成完整动作计划

## 9. 双存储协调与恢复

- [x] 9.1 先编写 SQLite 提交后 Chroma 成功/失败/部分成功状态测试
- [x] 9.2 实现 StorageCoordinator 的 SQLite 先提交、Chroma 后同步流程
- [x] 9.3 先编写相同 request_id 直接返回、仅修复索引和输入摘要冲突测试
- [x] 9.4 实现幂等请求短路与 failed/pending 操作重放
- [x] 9.5 先编写 active 全量 rebuild 和模型维度不兼容测试
- [x] 9.6 实现 Chroma repair/rebuild
- [x] 9.7 先编写用户归属校验、物理删除和删除同步失败恢复测试
- [x] 9.8 实现删除事务和可重试索引删除

## 10. 写入管线编排

- [x] 10.1 先编写从消息到 WriteReport 的 ADD/NOOP/UPDATE 全链路集成测试
- [x] 10.2 实现 MemoryWritePipeline 的请求级原子动作计划和组件编排
- [x] 10.3 添加零候选、抽取失败、关系歧义、SQLite 回滚和 Chroma retryable 集成测试
- [x] 10.4 验证每个候选步骤、历史命中、关系、动作和存储状态完整进入 WriteReport

## 11. API、CLI 与文档

- [x] 11.1 先编写 MemoryService list/show/delete/repair/rebuild 行为测试
- [x] 11.2 实现 MemoryService 公共 API
- [x] 11.3 先编写 `write/list/show/delete/sync repair/sync rebuild` CLI runner 测试
- [x] 11.4 实现 Typer + Rich CLI 的人类可读与 JSON 输出
- [x] 11.5 编写 README，记录安装、配置、输入 JSON、命令、数据语义和故障修复

## 12. 验证与交付

- [x] 12.1 运行完整 pytest 与覆盖率检查，确保覆盖率不低于 80%
- [x] 12.2 运行 ruff 并修复所有静态检查问题
- [x] 12.3 在独立 `agents_memory` 环境验证没有兄弟子项目导入、路径和运行时依赖
- [x] 12.4 使用临时存储演练 ADD、NOOP、UPDATE、跨用户隔离、幂等、删除和 repair/rebuild
- [ ] 12.5 使用真实密钥执行一次显式手工端到端写入并记录结果
- [x] 12.6 运行 GitNexus 变更检测，确认只影响预期符号和执行流，再进行提交或合并
