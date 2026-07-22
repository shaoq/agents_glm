# agents_rag

面向领域 / 企业文档问答的 RAG 工程 —— **知识构建（索引管线）核心闭环**。

把一批多格式文档（PDF / Markdown / HTML / Office）转成可检索、可溯源、支持增量维护的语义索引：高质量解析 → 结构感知父子分块 → embedding + BM25 双索引 → 内容指纹五态增量。

> 设计与原理见 `docs/specs/` 与 `docs/knowledge/`；变更管理见根目录 `openspec/changes/add-knowledge-base-core/`。

## 架构（索引管线 8 环节）

```
采集接入（内容指纹 + 五态）→ 文档解析（docling 降级链）→ 清洗归一化
    → 结构感知 + 父子分块 → embedding-3 向量化（缓存）→ 双索引（Chroma + BM25）
    → 元数据管理 → 持久化 + 增量维护
```

- **身份**：内容指纹（SHA-256）+ namespace，文档注册表（sqlite）为真相源
- **五态增量**：new / update / delete / move / skip，两阶段执行（先建新后删旧）
- **双索引**：向量（Chroma/HNSW）+ BM25（jieba + rank_bm25），`chunk_id` 对齐
- **父子分块**：子块建索引、父块存 KV，表格 / 代码豁免

## 模型

| 用途 | 模型 | 状态 |
|------|------|------|
| Embedding（向量化） | **embedding-3**（2048 维，默认智谱） | ✅ 本轮实际调用 |
| LLM（生成） | GLM-4.5（默认智谱） | ⏳ 查询侧，待实现 |
| Rerank（重排） | rerank-2（默认智谱） | ⏳ 查询侧，待实现 |

知识构建这条线**只用到 embedding-3**（文档切块向量化）；LLM / Rerank 属查询管线，本轮未实现。底层走 **OpenAI 兼容协议**（`openai` SDK），默认连智谱 BigModel；切换平台改 `.env` 的 `LLM_BASE_URL` + 模型名即可。

## 环境

共用 conda 环境 `agents_glm`（Python 3.12.13）：

```bash
conda activate agents_glm
pip install -e .[dev]        # 含 dev：pytest / ruff / reportlab
```

## 配置

复制 `.env.example` 为 `.env` 并填入密钥（真实 `.env` 不入库，已被 `.gitignore` 排除）：

```bash
cp .env.example .env
# 编辑 .env，至少设置 LLM_API_KEY（默认连智谱；换平台改 LLM_BASE_URL）
```

密钥缺失时 `ingest` 命令 fail-fast。

### 关键参数（`.env`）

| 参数 | 默认 | 说明 |
|------|------|------|
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | LLM 平台 OpenAI 兼容端点（换平台改这里） |
| `LLM_API_KEY` | — | 平台 API 密钥（缺失 fail-fast） |
| `EMBEDDING_DIM` | 2048 | 向量维度（256–2048 可选） |
| `EMBEDDING_MAX_BATCH` | 64 | embedding 单批最大条数（API 限制） |
| `EMBEDDING_MAX_CONCURRENCY` | 8 | embedding 并发上限（账号限流） |
| `CHUNK_SIZE` | 400 | 子块字符数（检索粒度） |
| `CHUNK_OVERLAP` | 64 | 相邻子块重叠**字符数**（≈ `CHUNK_SIZE` 的 16%，推荐 10–20%；调 `CHUNK_SIZE` 时需同步） |
| `PARENT_MAX_SIZE` | 1800 | 父块字符上限（由 LLM token 预算反推） |

> 分块参数口径为**字符**（中文 1 字 ≈ 1–2 token）。调参应配合 recall@k 评测集（本轮用经验锚点，评测闭环后置）。

## 用法

> 必须在 `agents_rag/` 目录下运行（`.env` 相对当前目录读取）。

```bash
conda activate agents_glm
cd agents_rag

# 索引一个目录（五态增量：未变跳过、变更更新、删除清理）
agents-rag ingest data/raw

# 或指定任意目录
agents-rag ingest tests/fixtures

# 禁用 PDF(docling) 解析（跳过重型依赖）
agents-rag ingest data/raw --no-pdf
```

**支持格式**：`.pdf .md .docx .xlsx .pptx .html .txt`（其他扩展名自动忽略）

**输出**：五态统计表（new / update / delete / move / skip / failed）+ 子块总数。

**增量行为**：再跑同一目录，未变文件全部 `skip`（不重复调 embedding、不花钱）；改内容 → `update`，删文件 → `delete`，移动文件 → `move`。

**清空重建**：

```bash
rm -rf storage/   # 删除索引产物，重跑即全量重建（data/raw 是种子）
```

## 目录结构

```
agents_rag/
├── pyproject.toml / .env.example / README.md
├── data/raw/          # 原始文档（gitignore）
├── storage/           # 索引产物（gitignore）
├── tests/{unit,integration,fixtures}/
└── src/agents_rag/
    ├── config.py / models.py / cli.py
    ├── ingestion/     # 指纹 / 注册表 / 五态 diff / 动作执行
    ├── parsing/       # base / docling / office / html / markdown / router / quality
    ├── cleaning/      # 归一化
    ├── chunking/      # 结构感知 / 父子
    ├── indexing/      # embedder / cache / vectorstore / chroma / bm25 / parent_store
    └── pipeline/      # ingest 编排
```

## 测试

```bash
pytest --cov=agents_rag --cov-report=term-missing   # 覆盖率 ≥ 80%（实测 89%）
```

## 状态与后续

- **已实现**：知识构建核心闭环（解析 / 分块 / 双索引 / 五态增量 / 轻量持久化 / CLI）
- **本轮后置**：孤儿清理、崩溃恢复、全量校准、minerU / 独立 OCR 降级、多模态、Contextual Retrieval、HITL、ACL / 多租户、知识图谱
- **后续阶段**：查询管线（检索 / RRF 融合 / Rerank / GLM 生成 / 引用校验 / RAGAS）→ FastAPI → Streamlit
