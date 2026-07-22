## Context

agents_rag 是面向领域 / 企业文档问答的 RAG 工程，基于智谱 GLM 全家桶（embedding / rerank / LLM 走云端 API）。已有「已批准待实现」的[整体设计 spec](../../../agents_rag/docs/specs/2026-07-21-agents-rag-design.md) 与详尽的[知识构建知识笔记](../../../agents_rag/docs/knowledge/rag-knowledge-base-construction.md)作为蓝本，并已产出[本轮实施设计](../../../agents_rag/docs/specs/2026-07-22-knowledge-base-core-implementation.md)。

当前状态：无任何可运行代码，conda `agents_glm` 环境几乎为空。本设计交付**知识构建核心闭环（索引管线）**——它是独立子系统，产出「可检索索引」，先行不依赖查询管线。

约束：
- 运行环境 conda `agents_glm`（Python 3.12.13）
- 智谱云端 API（embedding-3 付费），密钥经 `.env` 管理，缺失 fail-fast
- 本地优先、先小后大；多小文件 / 高内聚低耦合 / 数据不可变
- 单元测试覆盖率 ≥ 80%

## Goals / Non-Goals

**Goals:**

- 多格式文档高质量解析（docling 主力 PDF + Office / HTML / Markdown），统一 `Document` 模型 + 降级链
- 结构感知 + 父子分块（特殊结构豁免、完整溯源元数据）
- 双索引（向量 embedding-3 + BM25/jieba），`chunk_id` 对齐
- 内容指纹 + 文档注册表的五态增量；先建新后删旧 + `status` 中间态隔离
- 轻量持久化（Chroma 落盘 + embedding 缓存 + 注册表 + BM25 / 父块 pickle）
- CLI `agents-rag ingest` 跑通
- 关键接口抽象（`Parser` / `Embedder` / `VectorStore`）便于替换与离线测试

**Non-Goals:**

- 查询管线（检索 / 重排 / 生成 / 引用 / RRF 融合 / RAGAS）
- 重型运维：孤儿清理、崩溃恢复（WAL / 重放）、定期全量校准、superseded 延迟物理删除
- 进阶能力：minerU、独立 RapidOCR 降级链、多模态描述、Contextual Retrieval、HITL 解析置信度、ACL / 多租户、知识图谱、层级索引、蓝绿迁移
- 服务化（FastAPI / Streamlit）、鉴权、高并发

## Decisions

**1. 文档身份用内容指纹（SHA-256）而非路径。**
文件移动 / 改名不应改变身份；用路径作身份会在移动时误判为新文档导致重复索引。备选「部分 hash（头尾采样）」会漏检变更产生脏数据，不可接受。`doc_id = fingerprint + namespace` 解决多源同名冲突。

**2. 文档注册表（sqlite）为真相源，持有 `chunk_ids` 反查表。**
注册表是五态 diff 与精确删改（按 `chunk_id`）的根基。无它则删改只能「按 doc 模糊删」，残留 / 误删。备选「靠向量库 metadata 反查」不精确且跨 BM25 / 父块 KV 三处存储无法统一。

**3. 五态（含 move）+ 两阶段执行 + 先建新后删旧。**
`move` 仅更 `source_path` 不重索引（省时省钱）；「先建后删」避免空窗丢数据。备选「四态（无 move）」会让移动文件被误删 + 重建（浪费且空窗）；「先删后建」中间态丢数据。

**4. update 中间态：旧 chunk 标 `superseded` 不物理删（查询时 `status=active` 过滤）。**
避免新旧并存期脏读。本轮无查询侧，但 `version` + `status` 元数据**本轮就写入**，查询侧接入时零成本衔接。备选「物理立即删」有空窗风险；「蓝绿 doc 级 version」不如 chunk 级 `status` 轻（复用 metadata 过滤，无需额外机制）。

**5. 解析：docling 主力 + `ParserRouter` 降级链 + 统一 `Document` 模型。**
docling 是第三代文档理解模型，表格 / 版面结构化强；降级链保证单文件失败不拖垮整批；统一模型让下游（分块 / 索引）不关心来源格式。备选「第一代规则解析器」复杂 PDF 崩、「Unstructured」中文 PDF 弱、「首版即上 minerU」依赖过重故后置。

**6. 分块：结构感知 + 父子（子块检索、父块回传）。**
结构感知尊重语义边界、表格 / 代码豁免；父子分离粒度兼顾「检索精准」与「回传上下文全」。父块 = section，`parent_max_size` 由 LLM token 预算反推（约 1800 字）。备选「固定大小」切坏句子 / 表格、「句子窗口」不如父子灵活、「语义分块」需预计算 embedding（慢且贵）。

**7. 双索引：向量 embedding-3 + BM25(jieba)，`chunk_id` 对齐。**
向量负责语义召回，BM25 负责精确词召回（型号 / 编号 / 专有名词），互补。RRF 融合属查询侧，本轮只建双索引并保证对齐。备选「只向量」专有名词漏、「只 BM25」无语义。

**8. embedding 批处理 + 并发限流 + tenacity 重试 + 缓存（版本化键）。**
付费 API 工程化标配。缓存键 = `hash(text) + model + dim`，防止模型升级后命中旧向量缓存（极隐蔽 bug）。备选「无缓存」调参重跑全量付费且不可复现。

**9. `VectorStore` 抽象 + Chroma `PersistentClient` 起步。**
嵌入式零运维起步，抽象接口为规模化迁 Qdrant 留路；`PersistentClient` 传 path 即零成本落盘。备选「FAISS」要自包服务、「Qdrant」起步过重。

**10. 持久化分层：轻量层本轮做，重型工程后置。**
轻量层（Chroma path / embedding 缓存 / 注册表 / BM25 pickle）低成本且是省钱 / 增量根基；重型（孤儿清理 / 崩溃恢复 / 全量校准）后置不影响功能。备选「全后置」会导致每次 ingest 全量重 embed（真实密钥下持续付费）+ 五态增量失效；「全做」周期过长。

**11. `Embedder` / `VectorStore` 接口抽象 + Mock 测试。**
单元 / 集成测试用确定性 Mock 向量 + `tmp_path` Chroma，不依赖真实密钥与网络；真实密钥仅端到端验证用。备选「测试依赖真实密钥」不可离线、有费用、不稳定。

## Risks / Trade-offs

- **[docling 依赖重 + 首次模型下载慢]** → 接受首跑成本；PDF 复杂度不高时质量足够，minerU 后置兜底复杂场景。
- **[真实密钥有 API 费用]** → embedding 缓存（版本化键）+ 批处理摊薄；测试全 Mock 不烧钱。
- **[增量逻辑复杂、中间态可能不一致]** → 先建后删 + `status` 标记 + 写入前清残留 + 动作级 try/except 失败隔离；本轮后置崩溃恢复（接受弱恢复，靠重新 diff 收敛）。
- **[双索引不同步]** → `chunk_id` 对齐 + 同生命周期增删改；BM25 pickle 落盘 + 启动加载。
- **[父子分块 `parent_max_size` 靠经验锚点]** → 配置化；recall@k 评测调参后置（本轮用 400 / 64 / 1800 锚点）。
- **[无查询侧，`status` 过滤未实战验证]** → 元数据本轮写入并做索引一致性断言；查询侧接入时再端到端验证。
- **[中文 BM25 分词质量]** → jieba 分词 + 预留自定义词典接口（领域术语后置）。

## Migration Plan

- 首版交付，无既有索引需要迁移。
- 部署：`conda activate agents_glm` → `pip install -e .`（agents_rag）→ 配 `.env`（`ZHIPUAI_API_KEY`）→ `agents-rag ingest data/raw`。
- 回滚 / 重建：删除 `storage/` 清空索引，保留 `data/raw` 作为种子重跑 `ingest` 即可重建（embedding 缓存可加速）。

## Open Questions

- `chunk_size` / `overlap` / `parent_max_size` 精确值：本轮用经验锚点，recall@k 调参闭环后置。
- 智谱 Rerank 模型 ID：查询侧才用，后置按官方文档核实。
