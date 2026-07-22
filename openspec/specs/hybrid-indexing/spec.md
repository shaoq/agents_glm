# hybrid-indexing Specification

## Purpose
TBD - created by archiving change add-knowledge-base-core. Update Purpose after archive.
## Requirements
### Requirement: 智谱 embedding 向量化
系统 SHALL 用智谱 embedding-3 对子块批量向量化：单批不超过 64 条、单条不超过 3072 tokens；并发在途请求数受配置上限约束；可重试错误（429 / 5xx）SHALL 指数退避重试，不可重试错误（鉴权 / 参数）不重试。

#### Scenario: 超批量自动分批
- **WHEN** 待向量化的子块数超过单批上限
- **THEN** 系统自动分多次批量请求

#### Scenario: 限流时重试
- **WHEN** embedding API 返回 429
- **THEN** 系统按指数退避重试，最终成功或抛出可重试错误

### Requirement: embedding 缓存
系统 SHALL 以 `hash(text) + model + dim` 为键缓存 embedding 向量；文本、模型、维度均不变时复用缓存、不调用 API。

#### Scenario: 相同文本命中缓存
- **WHEN** 对未变更文本再次向量化
- **THEN** 命中缓存、不调用 embedding API

#### Scenario: 模型 / 维度变更不命中旧缓存
- **WHEN** embedding 模型或维度变更
- **THEN** 旧缓存不命中、重新向量化（避免向量空间错配）

### Requirement: 向量索引（Chroma）
系统 SHALL 将子块向量与元数据写入 Chroma（HNSW，`PersistentClient` 落盘），通过抽象 `VectorStore` 接口暴露 `upsert` / `delete_by_doc` / `query`。

#### Scenario: upsert 后可按向量查询
- **WHEN** 向 Chroma 写入子块向量
- **THEN** 可按向量近邻查询并返回对应 chunk_id 与元数据

#### Scenario: 按文档删除
- **WHEN** 调用 `delete_by_doc(doc_id)`
- **THEN** 该文档的所有子块从向量库移除

### Requirement: BM25 索引
系统 SHALL 用 jieba 分词 + rank_bm25 构建 BM25 索引并 pickle 持久化；BM25 索引条目 SHALL 与向量库以相同 `chunk_id` 对齐。

#### Scenario: 专有名词可被召回
- **WHEN** 查询含专有名词 / 编号的词
- **THEN** BM25 能召回含该词的 chunk

#### Scenario: 与向量库 chunk_id 对齐
- **WHEN** 索引构建完成
- **THEN** 向量库与 BM25 中同一 `chunk_id` 指向同一文本

### Requirement: 父块 KV 存储
系统 SHALL 将父块写入父块 KV（按文档维度组织），不建向量索引；支持按 `parent_id` 取文本、按 `doc_id` 删除。

#### Scenario: 按 parent_id 取父块
- **WHEN** 给定子块的 parent_id
- **THEN** 能从父块 KV 取回对应父块全文

### Requirement: 双索引一致性
向量库、BM25、父块 KV SHALL 以 `chunk_id` / `doc_id` 对齐并同生命周期增删改；任一子块的写入 / 删除 SHALL 在三者协同完成。

#### Scenario: 删除文档时三索引同步
- **WHEN** 删除一个文档
- **THEN** 其子块从向量库与 BM25 移除、父块从 KV 移除，无残留

