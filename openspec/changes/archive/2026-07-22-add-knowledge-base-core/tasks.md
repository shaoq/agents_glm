# Implementation Tasks

> 实现依据：[proposal](./proposal.md) · [design](./design.md) · [specs](./specs/) · [实施设计](../../../agents_rag/docs/specs/2026-07-22-knowledge-base-core-implementation.md)
>
> 运行环境：conda `agents_glm`（Python 3.12.13）。每层完成即写测试，覆盖率 ≥ 80%。

## 1. 项目骨架与配置

- [x] 1.1 创建 `pyproject.toml`（PEP 621 元信息 + 依赖 + `[project.scripts] agents-rag = "agents_rag.cli:app"` + `dev` extras）
- [x] 1.2 创建 `src/agents_rag/` 包结构（`__init__.py` + 子包 `__init__.py`：`ingestion/` `parsing/` `cleaning/` `chunking/` `indexing/` `pipeline/`）
- [x] 1.3 实现 `config.py`（pydantic-settings 加载 `.env`：`ZHIPUAI_API_KEY` / `EMBEDDING_*` / `CHUNK_*` / `PARENT_MAX_SIZE` / `DATA_DIR` / `STORAGE_DIR`；密钥缺失 fail-fast）
- [x] 1.4 创建 `.env.example`（密钥与参数模板；真实 `.env` 由 `.gitignore` 排除）
- [x] 1.5 在 conda `agents_glm` 安装依赖（`pip install -e .[dev]`），验证 `agents-rag --help` 可执行

## 2. 数据结构（models）

- [x] 2.1 实现 `models.py`：`Document` / `Section` / `Block` / `ParentChunk` / `ChildChunk` / `QualityReport` / `Action` / `DocumentRecord`（全部 pydantic `frozen=True`）
- [x] 2.2 单测：模型不可变、默认值、序列化往返

## 3. 采集与注册表（ingestion）

- [x] 3.1 实现 `fingerprint.py`：流式 SHA-256（1MB buffer）+ `(size, mtime)` 预筛
- [x] 3.2 单测：流式与一次性 hash 一致；预筛不漏判变更
- [x] 3.3 实现 `registry.py`：`DocumentRegistry`（sqlite，`upsert` / `get` / `delete` / `list` / `diff`，跨会话持久）
- [x] 3.4 实现 `collector.py`：扫描目录 + 五态 diff（new / update / delete / move / skip；含复制 vs 移动、多源同名边界）
- [x] 3.5 单测：五态各场景判定正确
- [x] 3.6 实现 `actions.py`：`Action` 类型与两阶段执行编排（先 new / update，后 delete / move）

## 4. 解析（parsing + cleaning）

- [x] 4.1 实现 `parsing/base.py`：`Parser` ABC（`parse(path) -> Document`）
- [x] 4.2 实现 `markdown_parser.py`（标题层级 → sections）
- [x] 4.3 实现 `html_parser.py`（trafilatura 正文 + bs4 清洗 → sections）
- [x] 4.4 实现 `office_parser.py`（python-docx / openpyxl / python-pptx → sections + 表格 block）
- [x] 4.5 实现 `docling_parser.py`（PDF → sections + 表格 block + page）
- [x] 4.6 实现 `quality.py`：`QualityReport` + `is_poor`（chars_per_page / garbage_ratio 阈值）
- [x] 4.7 实现 `router.py`：`ParserRouter`（按扩展名路由 + 质量评估降级 + 全失败跳过并记日志）
- [x] 4.8 实现 `cleaning/normalizer.py`：按 `doc_type` 轻量归一化（保留 page / heading，不动结构）
- [x] 4.9 单测：各 parser 产出统一 `Document`；表格 block 保留；清洗不误删编号、保留元数据；router 全失败跳过不中断

## 5. 分块（chunking）

- [x] 5.1 实现 `chunking/base.py`：`Chunker` ABC
- [x] 5.2 实现 `structural.py`：结构感知切父块（按 section，受 `parent_max_size` 约束，超切小合；表格 / 代码豁免）
- [x] 5.3 实现 `parent_child.py`：父块切子块（`chunk_size` / `overlap`；子块 id 编码 `parent_id`；元数据带 `version=1` / `status=active`）
- [x] 5.4 单测：不切段落 / 表格；子块 id 编码 parent_id；父块不进向量；元数据完整可溯源

## 6. 向量化与缓存（indexing/embedder + cache）

- [x] 6.1 实现 `cache.py`：`EmbeddingCache`（sqlite，键 = `hash(text)+model+dim`，`get` / `put`）
- [x] 6.2 单测：键版本化（换 model / dim 不命中旧缓存）
- [x] 6.3 实现 `embedder.py`：`Embedder` ABC + `ZhipuEmbedder`（embedding-3；批量 ≤64 + 信号量并发 + tenacity 重试 + 查缓存）
- [x] 6.4 单测：超量分批；429 退避重试（mock 客户端）；缓存命中不调 API

## 7. 双索引（indexing）

- [x] 7.1 实现 `vectorstore.py`：`VectorStore` ABC（`upsert` / `delete_by_doc` / `query`）
- [x] 7.2 实现 `chroma_store.py`：`ChromaStore`（`PersistentClient` 落盘；metadata 携带 chunk 元数据）
- [x] 7.3 实现 `bm25_index.py`：`BM25Index`（jieba 分词 + rank_bm25；`index` / `query`；pickle `save` / `load`）
- [x] 7.4 实现 `parent_store.py`：`ParentStore`（文档维度 KV；`get` / `put` / `delete_by_doc`）
- [x] 7.5 单测：Chroma upsert / query / delete_by_doc（tmp_path）；BM25 分词召回 + chunk_id 对齐；按 doc 同步删三索引无残留

## 8. 索引管线编排（pipeline/ingest）

- [x] 8.1 实现 `pipeline/ingest.py`：`IngestPipeline`（collector → router → normalizer → chunker → embedder → 三索引 → registry；两阶段执行；写入前清残留；update 先建新后标旧 `superseded`；动作级失败隔离）
- [x] 8.2 集成测试（Mock Embedder + tmp_path）：全量 ingest 一批 fixtures → 三索引 chunk_id 对齐 + 注册表一致
- [x] 8.3 集成测试：二次 ingest 未变文件全 skip、不调 embedding；update 后旧 chunk 标 `superseded`；delete 清三索引

## 9. CLI

- [x] 9.1 实现 `cli.py`：Typer `ingest <dir>` 子命令（Rich 输出五态统计 + 索引规模 + 失败日志）
- [x] 9.2 验证 `agents-rag ingest tests/fixtures` 跑通

## 10. 端到端验证与收尾

- [x] 10.1 准备 `tests/fixtures` 小文档集（md / txt / html / docx / pdf）
- [x] 10.2 真实密钥端到端：`agents-rag ingest tests/fixtures` 成功产出索引
- [x] 10.3 真实密钥端到端：二次 ingest 增量跳过验证
- [x] 10.4 覆盖率 ≥ 80%（`pytest --cov`），补齐测试
- [x] 10.5 更新 `agents_rag/README.md`（环境 / 安装 / `ingest` 用法）
