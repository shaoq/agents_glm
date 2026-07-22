# agents_rag 知识构建核心闭环 · 实施设计

| 项 | 值 |
|----|----|
| 日期 | 2026-07-22 |
| 状态 | 已批准，待实现 |
| 范围 | 知识构建（索引管线）核心闭环 |
| 运行环境 | conda 环境 `agents_glm`（Python 3.12.13） |
| 上游文档 | [整体设计 spec](./2026-07-21-agents-rag-design.md) · [知识构建知识笔记](../knowledge/rag-knowledge-base-construction.md) |

---

## 0. 文档定位

本文是 [整体设计 spec](./2026-07-21-agents-rag-design.md) §15「MVP 实现顺序」步骤 1–6 的**实施细化**，聚焦本轮交付的「知识构建核心闭环」：范围界定、目录细化、最终数据结构与接口、实现顺序、持久化分层边界。

- 讲「本轮具体做什么、目录怎么落、接口怎么定、做到哪停」
- 原理与方案取舍见 [知识构建知识笔记](../knowledge/rag-knowledge-base-construction.md)（本文引用其小节，不重复）

**本轮不实现查询管线**（检索 / 重排 / 生成 / 引用）。知识构建产出「可检索索引」，是独立可交付的子系统，先行不依赖查询侧。

---

## 1. 本轮范围（核心闭环）

知识构建 8 环节中，本轮实现**主线全链路 + 增量骨架 + 轻量持久化**：

| 环节 | 本轮实现 | 笔记依据 |
|------|---------|---------|
| 采集 / 注册表 | 内容指纹（SHA-256 流式 + `(size, mtime)` 预筛）+ 文档注册表（sqlite，真相源）+ 五态 diff + 两阶段执行 + 先建新后删旧 + `status` 标记中间态 | §2 |
| 解析 | `ParserRouter` 降级链 + **docling 主力（PDF）** + Office / HTML / Markdown + 统一 `Document(sections→blocks)` 模型 + 解析质量评估 | §3 |
| 清洗 | 轻量归一化（空白 / 全半角 / 去页眉页脚），按 `doc_type` 配置，**不动结构、保留 page/heading** | §4 |
| 分块 | 结构感知分块（尊重 block 边界，表格 / 代码豁免）+ **父子分块**（父块 = section 存 KV，子块建向量 + BM25 索引） | §5 |
| 向量化 | 智谱 **embedding-3**（批量 ≤64 + 并发限流 + tenacity 重试）+ **embedding 缓存**（sqlite，键 = `hash(text)+model+dim` 版本化） | §6.4 / 6.5 / 6.13 |
| 索引 | Chroma（HNSW，`PersistentClient` 落盘，`VectorStore` 抽象）+ BM25（jieba + rank_bm25，pickle 落盘）+ 父块 KV，`chunk_id` 对齐 | §6.6 / 6.7 |
| 元数据 | chunk metadata schema（doc_id / source / page / heading / section_path / parent_id / version / status / block_type / char_span） | §7 |
| 持久化 | **轻量层本轮做**：Chroma path + embedding 缓存 sqlite + 文档注册表 sqlite + BM25 / 父块 pickle 落盘 | §8 |
| 驱动 | 最小 CLI `agents-rag ingest <dir>`（Typer + Rich），输出五态统计与索引规模 | spec §15 |

### 持久化分层边界（本轮 vs 后置）

「持久化」不是整体，分轻量层（低成本、是省钱 / 增量根基）与重型工程层（运维加固）：

- **本轮做（轻量层）**：Chroma `PersistentClient`(传 path)、embedding 缓存 sqlite、文档注册表 sqlite、BM25 / 父块 pickle 落盘 + 启动加载。
  - 理由：embedding 缓存避免每次 ingest 全量重付费 embed（真实密钥硬需求）；注册表持久化让五态 diff 跨会话生效（否则增量逻辑白写）。
- **本轮后置（重型工程）**：孤儿清理、崩溃恢复（WAL / 操作日志重放 / checkpoint）、定期全量校准。
  - 理由：后置不影响核心闭环功能与成本，属运维加固，见笔记 §2.9 / §8.4 / §8.8。

---

## 2. 目录结构

在 [整体 spec §6](./2026-07-21-agents-rag-design.md) 基础上做两处细化：

1. **新增 `ingestion/`**：承载采集 + 注册表 + 五态。这些是可独立测试的核心骨架，原 spec 隐含在 `pipeline/ingest.py`，独立成模块更内聚、更易测。
2. **新增 `cleaning/`**：清洗是解析与分块之间的独立净化层（笔记 §4），独立成模块。
3. 数据结构集中到 `models.py`。

```
agents_rag/
├── README.md
├── pyproject.toml                     # PEP 621，依赖 + 入口脚本
├── .env.example                       # 密钥模板（真实 .env 不入库）
├── docs/                              # 已有 specs/ knowledge/
├── data/                              # 原始文档（gitignore）
│   └── raw/
├── storage/                           # 持久化产物（gitignore）
│   ├── chroma/
│   ├── bm25.pkl
│   ├── parents/                       # 父块 KV（按 doc_id 分目录）
│   ├── embedding_cache.sqlite
│   └── registry.sqlite
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/                      # 小测试文档（md/txt/html/docx/pdf）
└── src/agents_rag/
    ├── __init__.py
    ├── config.py                      # pydantic-settings
    ├── models.py                      # 数据结构（pydantic frozen）
    ├── cli.py                         # Typer 入口：ingest
    ├── ingestion/                     # 采集 + 注册表 + 五态（细化新增）
    │   ├── __init__.py
    │   ├── fingerprint.py             # 流式 SHA-256 + (size,mtime) 预筛
    │   ├── registry.py                # DocumentRegistry（sqlite）
    │   ├── collector.py               # 扫描 + 五态 diff
    │   └── actions.py                 # Action 类型 + 两阶段执行编排
    ├── parsing/
    │   ├── __init__.py
    │   ├── base.py                    # Parser ABC
    │   ├── docling_parser.py          # PDF 主力
    │   ├── office_parser.py           # docx / xlsx / pptx
    │   ├── html_parser.py             # trafilatura + bs4
    │   ├── markdown_parser.py
    │   ├── router.py                  # ParserRouter 降级链
    │   └── quality.py                 # QualityReport + is_poor
    ├── cleaning/
    │   ├── __init__.py
    │   └── normalizer.py              # 按 doc_type 清洗
    ├── chunking/
    │   ├── __init__.py
    │   ├── base.py                    # Chunker ABC
    │   ├── structural.py              # 结构感知分块
    │   └── parent_child.py            # 父子分块
    ├── indexing/
    │   ├── __init__.py
    │   ├── embedder.py                # Embedder ABC + ZhipuEmbedder
    │   ├── cache.py                   # EmbeddingCache（sqlite，版本化键）
    │   ├── vectorstore.py             # VectorStore ABC
    │   ├── chroma_store.py            # ChromaStore（PersistentClient）
    │   ├── bm25_index.py              # BM25Index（jieba + rank_bm25，pickle）
    │   └── parent_store.py            # ParentStore（KV，文档维度存储）
    └── pipeline/
        ├── __init__.py
        └── ingest.py                  # IngestPipeline 编排
```

> 查询侧目录（`retrieval/` `generation/` `citation/` `eval/`）本轮**不建**。

---

## 3. 核心数据结构（pydantic，不可变）

整合 [整体 spec §7](./2026-07-21-agents-rag-design.md) 与知识笔记各小节的细化，定稿如下（全部 `frozen=True`）：

```python
# —— 文档注册表（sqlite，真相源；笔记 §2.3）——
class DocumentRecord:
    doc_id: str               # = content_fingerprint + ":" + namespace，主键
    content_fingerprint: str  # SHA-256(content)
    source_path: str          # 当前路径（移动时更新）
    source_namespace: str     # local / oss / crawl，默认 "local"
    doc_type: str             # pdf | markdown | docx | xlsx | pptx | html | txt
    chunk_ids: list[str]      # 该文档所有子块 id（删改精确句柄）
    parent_chunk_ids: list[str]
    version: int
    content_size: int         # 预筛 + 校验
    indexed_at: datetime
    status: str               # active / deleted

# —— 解析输出（统一模型；笔记 §3.4）——
class Document:
    doc_id: str
    source: str
    doc_type: str
    sections: list[Section]
    metadata: dict

class Section:
    heading: str | None
    level: int                # H1=1, H2=2 ...
    blocks: list[Block]
    children: list[Section]

class Block:
    type: str                 # paragraph | table | list | code
    text: str
    page: int | None
    bbox: tuple | None
    table_data: dict | None   # type=table 时：行列矩阵 / markdown 表

# —— 分块产物（笔记 §5.14）——
class ParentChunk:            # 父块，存 KV，不建向量索引
    id: str
    doc_id: str
    text: str
    page: int | None
    heading: str | None
    section_path: str

class ChildChunk:             # 子块，建向量 + BM25 索引
    id: str                   # 编码 parent_id，如 f"{parent_id}__{idx}"
    parent_id: str
    doc_id: str
    text: str
    page: int | None
    heading: str | None
    section_path: str
    block_type: str           # paragraph | table | list | code（表格豁免回传用）
    char_span: tuple[int, int]
    version: int
    status: str               # active / superseded（笔记 §2.10）
    # embedding 不存对象，仅存向量库

# —— 解析质量评估（笔记 §3.9）——
class QualityReport:
    chars_per_page: float
    table_count: int
    heading_depth: int
    garbage_ratio: float

# —— 五态动作（笔记 §2.6）——
class Action:
    kind: str                 # new | update | delete | move | skip
    doc_id: str
    source_path: str
    fingerprint: str
    doc_type: str
    old_record: DocumentRecord | None  # update/delete/move 用
```

---

## 4. 关键接口（抽象 → 实现）

| 接口 | 方法 | 本轮实现 | 笔记依据 |
|------|------|---------|---------|
| `Parser` | `parse(path) -> Document` | Docling / Office / HTML / Markdown | §3 |
| `ParserRouter` | `parse(path) -> Document \| None` | 按扩展名路由 + 质量评估降级，全失败跳过+日志 | §3.6 / 3.9 |
| `Normalizer` | `normalize(doc) -> Document` | 按 doc_type 轻量归一化 | §4 |
| `Chunker` | `chunk(doc) -> (list[ParentChunk], list[ChildChunk])` | 结构感知 + 父子 | §5.4 / 5.14 |
| `Embedder` | `embed(texts) -> list[Vec]` | ZhipuEmbedder（批量 + 并发 + 重试 + 缓存） | §6.4 |
| `EmbeddingCache` | `get/put(key)` | sqlite，key = `hash(text)+model+dim` | §6.5 / 6.13 |
| `VectorStore` | `upsert / delete_by_doc / query` | ChromaStore（PersistentClient） | §6.8 |
| `BM25Index` | `index / query / save / load` | jieba + rank_bm25，pickle | §6.6 / 6.7 |
| `ParentStore` | `get / put / delete_by_doc` | 文档维度 KV（笔记 §12.1.1 存储路径） | §5.5 |
| `DocumentRegistry` | `upsert / get / delete / list / diff` | sqlite | §2.3 / 8.2 |
| `Collector` | `scan(dir) -> list[scan项]` | 流式 hash + 预筛 | §2.4 |
| `IngestPipeline` | `run(dir) -> IngestReport` | 编排：两阶段执行 + 先建后删 + status | §2.6 / 2.10 |

---

## 5. 索引管线编排（pipeline/ingest.py）

笔记 §2.6 / §2.10 的状态机落地：

```
scan(data/raw/)                         # Collector：流式 hash + (size,mtime) 预筛
  → registry.diff(scan_items)           # 五态 → Action list（阶段1：检测）
  → 两阶段执行（阶段2）：
      阶段 2a 先建后删：new / update
        update: 写入前先清该 doc_id 残留 chunk（笔记 §2.9 写入前清残留）
        parse → normalize → chunk(父子) → embed(查缓存) → 写三索引(子块入向量库+BM25, 父块入KV)
        新 chunk 标 status=active；旧 chunk 标 status=superseded（不物理删）
        registry.upsert(doc_id, version++, chunk_ids)
      阶段 2b 删除/移动：delete（源文件已移除 → 按 chunk_ids 物理删三索引 + 缓存） / move（仅更 source_path）
      skip：无操作
  → 返回 IngestReport（五态计数 + 索引规模 + 跳过/失败日志）
```

要点（笔记 §2.6「保证正确更新」）：
1. `chunk_id` 精确句柄删改；2. 先建新后删旧；3. 多索引（向量 + BM25 + 父块 + 缓存）协同；4. 幂等（new 前查存在、delete 不存在不报错）；5. 先 new/update 再 delete；6. 动作级 try/except 失败隔离；7. 下游（parser/chunker/embedder）纯函数。

> 中间态隔离：update 的旧 chunk 标 `superseded` 不物理删（笔记 §2.10）；其**延迟批量物理删除本轮后置**（同属延迟清理，见 §10）——本轮标记后留存，查询侧用 `status=active` 过滤即不影响召回。但 chunk 元数据**本轮就写入** `version` + `status`，为后续零成本衔接。

---

## 6. 实现顺序（自底向上，每层带单元测试）

合并实施（不分 Phase），按依赖顺序逐步实现，每层完成即写测试：

1. **项目骨架**：`pyproject.toml`（PEP 621 + 入口脚本）+ `src` 布局 + `.env.example` + `config.py` + 装核心依赖到 conda `agents_glm`。
2. **`models.py`**：§3 全部数据结构（pydantic frozen）。
3. **`ingestion/`**：`fingerprint.py`（流式 hash + 预筛）→ `registry.py`（DocumentRegistry sqlite）→ `collector.py`（扫描 + 五态 diff）→ `actions.py`。
4. **`parsing/`**：`base.py` → `markdown_parser.py` / `html_parser.py` / `office_parser.py` → `docling_parser.py` → `quality.py` → `router.py`（降级链）。
5. **`cleaning/normalizer.py`**：按 doc_type 归一化。
6. **`chunking/`**：`base.py` → `structural.py` → `parent_child.py`（父块 = section，`parent_max_size` 由 token 预算反推，笔记 §5.15）。
7. **`indexing/embedder.py` + `cache.py`**：ZhipuEmbedder（批量 64 + 并发限流 + tenacity）+ EmbeddingCache（版本化键）。
8. **`indexing/`**：`vectorstore.py`(ABC) → `chroma_store.py`(PersistentClient) → `bm25_index.py`(jieba+rank_bm25+pickle) → `parent_store.py`(KV)。
9. **`pipeline/ingest.py`**：IngestPipeline 编排（§5）。
10. **`cli.py`**：`agents-rag ingest <dir>` 子命令。
11. **测试补齐 + 端到端验证**：真实密钥跑 `tests/fixtures` 小文档集，二次 ingest 验证增量跳过。

---

## 7. 配置（.env + pydantic-settings）

```dotenv
# .env.example（真实 .env 不入库）
ZHIPUAI_API_KEY=
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIM=2048
EMBEDDING_MAX_BATCH=64
EMBEDDING_MAX_CONCURRENCY=8
CHUNK_SIZE=400
CHUNK_OVERLAP=64
PARENT_MAX_SIZE=1800
DATA_DIR=./data
STORAGE_DIR=./storage
```

- 启动校验 `ZHIPUAI_API_KEY`，缺失 fail-fast（spec §11）。
- 查询侧参数（`VECTOR_TOP_K` / `BM25_TOP_K` / `RERANK_TOP_N` / `LLM_*`）本轮不实现，不放。

---

## 8. 依赖（pyproject.toml 核心）

```toml
[project]
name = "agents-rag"
requires-python = ">=3.12"
dependencies = [
    "zhipuai",
    "chromadb",
    "rank-bm25",
    "jieba",
    "docling",
    "python-docx", "openpyxl", "python-pptx",
    "trafilatura", "beautifulsoup4", "lxml",
    "pydantic", "pydantic-settings",
    "typer", "rich",
    "tenacity",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "ruff"]

[project.scripts]
agents-rag = "agents_rag.cli:app"
```

> 本轮**不装**：`mineru`、`rapidocr-onnxruntime`（docling 内置 OCR 选项可按需启用，独立降级链后置）、`ragas`/`datasets`（查询侧评估）、`fastapi`/`streamlit`（后续阶段）。装到 conda `agents_glm`。docling 会触发模型下载，首跑较慢。

---

## 9. 测试策略

覆盖率目标 ≥ 80%（spec §12）。

- **单元测试（纯逻辑，离线，不依赖密钥）**：
  - `fingerprint`：流式 hash 与一次性 hash 一致；`(size,mtime)` 预筛不漏判。
  - `registry`：upsert / diff 五态（new/update/delete/move/skip）边界（复制 vs 移动、多源同名）。
  - `normalizer`：保留 page/heading、不误删表格编号。
  - `chunking`：结构感知边界（不切段落/表格）、表格/代码豁免、父子 id 编码。
  - `cache`：键版本化（换 model/dim 不命中旧缓存）。
  - `bm25`：jieba 分词、chunk_id 对齐。
- **集成测试（离线）**：`Embedder` 用确定性 Mock 向量；Chroma `PersistentClient(tmp_path)`；端到端 ingest 一批 `tests/fixtures` 文档，断言三索引 chunk_id 对齐、注册表一致。
- **端到端（真实密钥，手动）**：`agents-rag ingest tests/fixtures`，二次运行验证未变文件跳过、增量生效。

> Embedder / VectorStore 均做接口抽象，单测与集成测用 Mock / tmp 路径，不依赖真实密钥；真实密钥仅端到端验证用。

---

## 10. 后置清单（本轮明确不做）

- 孤儿清理（异常中断残留的兜底扫描，与五态 delete 动作不同）、superseded 旧 chunk 的延迟批量物理删除、崩溃恢复（WAL / 重放 / checkpoint）、定期全量校准（§1 持久化重型层）。
- minerU、独立 RapidOCR 降级链、多模态描述、Contextual Retrieval。
- 解析置信度 + HITL、ACL / 多租户、知识图谱、层级索引、蓝绿迁移。
- **查询管线全部**（检索 / 重排 / 生成 / 引用 / RAGAS）。

---

## 11. 验收标准

- [ ] `agents-rag ingest data/raw` 跑通，产出 Chroma + BM25 + 注册表 + 缓存，输出五态统计。
- [ ] docling 解析 PDF 出结构化 `Document`（sections + blocks + page）。
- [ ] 二次 ingest：未变文件 skip，更新文件先建新后删旧（status 标记）。
- [ ] 单测覆盖率 ≥ 80%，全绿。
- [ ] 真实密钥端到端验证一批小文档成功。
