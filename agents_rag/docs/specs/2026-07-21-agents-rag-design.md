# agents_rag 设计文档

| 项 | 值 |
|----|----|
| 日期 | 2026-07-21 |
| 状态 | 已批准，待实现 |
| 运行环境 | conda 环境 `agents_glm`（Python 3.12.13） |
| 工作区 | `agents_glm/` monorepo 根下的子项目 |

---

## 1. 概述

agents_rag 是一个面向**领域/企业文档问答**的 RAG（检索增强生成）工程。底层走 **OpenAI 兼容协议**（默认连智谱 GLM 全家桶：LLM + Embedding + Rerank 全部走云端 API，换平台改 `.env` 的 `LLM_BASE_URL` + 模型名即可），支持 PDF / Markdown / Office / 网页 多格式文档，采用**混合检索（向量 + BM25）+ Rerank 重排**，强调准确性、引用溯源与抗幻觉，按 **CLI → FastAPI → Web UI** 渐进式交付，架构支持「先小后大」扩展。

## 2. 目标与非目标

**目标（第一版 CLI 即覆盖）：**
- 多格式文档接入与高质量解析（含 PDF 表格/版面、扫描件 OCR）
- 结构感知分块 + 父子分块
- 混合检索（向量 + BM25）+ RRF 融合 + Rerank 精排（默认智谱）
- 引用溯源（文档名 + 页码 + 片段）与抗幻觉兜底
- 增量索引（文档指纹 hash，支持增删改）
- RAGAS 自动评估回归
- CLI 一步到位跑通完整链路

**非目标（后续阶段）：**
- 多用户鉴权 / 高并发服务化（FastAPI 阶段再考虑）
- 多模态（图片/视频/音频）问答
- 模型微调
- 分布式 / 大规模向量库服务化（架构预留接口，Qdrant 迁移路径）

## 3. 需求与约束（决策汇总）

| 维度 | 决策 |
|------|------|
| 场景 | 领域/企业文档问答（重准确性、引用、稳定） |
| 文档类型 | PDF（含复杂版面/扫描件）+ Markdown/纯文本 + Office(Word/Excel/PPT) + 网页/HTML |
| LLM | GLM-4.5（默认智谱，可升级 GLM-5.2）/ Flash 系列（低成本高频） |
| Embedding | embedding-3（默认智谱），默认 2048 维（可自定义 256–2048） |
| Rerank | Rerank API（默认智谱 rerank-2，文本重排序） |
| 框架路线 | 自研混合（自研管线编排 + 专门库，不绑大框架） |
| 解析器 | Docling 主力 + 诊断式降级（`poor_reason` 区分 scan/layout → RapidOCR/MinerU 兜底，只降一级） |
| 检索策略 | 向量 + BM25 双路 → RRF 融合 → Rerank 重排 |
| 向量库 | Chroma（起步，嵌入式）→ Qdrant（规模化，接口已抽象） |
| 交付形态 | 渐进式：CLI（Typer）→ FastAPI → Streamlit |
| 数据规模 | 先小后大，架构预留扩展 |
| 部署 | 本地优先 |
| 依赖管理 | `pyproject.toml`（PEP 621），装到 conda `agents_glm` |

## 4. 整体架构

RAG 由两条流水线组成：**索引管线（离线）**与**查询管线（在线）**。

```
─── 索引管线（离线：文档 → 索引）───
[文档源] → router 选 Parser → 解析 → 清洗归一化
        → 结构感知分块(+父子, +元数据:来源/页码/标题)
        → 批量 Embedding(embedding-3, 带缓存)
        → upsert 向量库(Chroma) + 建 BM25 索引(jieba + rank_bm25)
        → 记录文档指纹(hash) 用于增量

─── 查询管线（在线：问题 → 带引用回答）───
[用户问题] → (查询改写/HyDE 可选)
          → 向量召回 + BM25 召回
          → RRF 融合
          → Rerank 精排
          → 上下文构建(去重 + token 预算 + 引用编号注入)
          → GLM-4.5 生成(仅基于上下文 + 强制引用)
          → 引用校验(编号必须命中检索结果)
          → [带引用来源的回答]
```

## 5. 技术选型

| 层 | 选型 | 备选 | 说明 |
|----|------|------|------|
| LLM | GLM-4.5（默认智谱，可升 GLM-5.2） | GLM-4.5-Flash | 主力用 4.5，高频低成本用 Flash |
| Embedding | embedding-3（默认智谱，2048 维） | embedding-2 | 256–2048 可选，8K 上下文，0.5 元/百万 tokens |
| Rerank | Rerank API（默认智谱 rerank-2） | 本地 bge-reranker-v2-m3 | 原生零运维，无需 GPU |
| 向量库 | Chroma（嵌入式） | FAISS / Qdrant / Milvus | 抽象 `VectorStore` 接口后可平滑迁移 |
| 关键词检索 | rank_bm25 + jieba | bm25s | 中文 BM25 必须配分词 |
| 混合融合 | RRF（Reciprocal Rank Fusion） | 加权得分 | 无需调参、鲁棒 |
| 文档解析 | Docling + 诊断式降级（MinerU/RapidOCR 兜底） | Unstructured | 见 §13 难点 1 |
| OCR | RapidOCR（onnxruntime） | PaddleOCR | 扫描件，CPU 可跑 |
| Office | python-docx / openpyxl / python-pptx | — | Word/Excel/PPT |
| HTML | trafilatura + BeautifulSoup | — | 正文抽取 + 噪音清洗 |
| SDK | openai | — | OpenAI 兼容协议，默认连智谱 BigModel，换平台改 `LLM_BASE_URL` |
| 配置/密钥 | pydantic-settings + .env | — | 密钥绝不入库 |
| 重试 | tenacity | — | API 限流指数退避 |
| CLI | Typer + Rich | Click | 子命令：ingest / ask / eval |
| API（后期） | FastAPI + Uvicorn | — | — |
| UI（后期） | Streamlit | Gradio | — |
| 评估 | RAGAS + datasets | 自定义指标 | faithfulness / answer_relevancy / context_recall |
| 测试 | pytest + pytest-asyncio | — | 单元 + 集成 |

### Embedding-3 关键参数（已核实）
- 向量维度：256–2048，默认 **2048**
- 上下文窗口：8K；单条文本 ≤ 3072 tokens；单次请求 ≤ 64 条
- 价格：0.5 元 / 百万 tokens
- 并发限制（在途请求数）：V0=50 / V1=100 / V2=300 / V3=500（决定 embedding 批处理并发上限）

### Rerank
- 文本重排序 API（默认智谱）：输入 query + candidate 文本列表，返回各候选相关性分数
- 模型 ID 通过 `RERANK_MODEL` 配置，实现首日按官方文档 [docs.bigmodel.cn](https://docs.bigmodel.cn) 核实填入（候选默认 `rerank-2`）

## 6. 目录结构

遵循「多小文件 / 功能域组织 / 高内聚低耦合」：

```
agents_rag/
├── README.md
├── pyproject.toml                 # 依赖与元信息（PEP 621）
├── .env.example                   # 密钥模板（不含真实值）
├── docs/specs/                    # 设计文档
├── data/                          # 原始与中间文档（gitignore）
│   ├── raw/
│   └── processed/
├── storage/                       # 向量库与 BM25 索引（gitignore）
│   ├── chroma/
│   └── bm25/
├── eval/datasets/                 # 评测集
├── tests/
│   ├── unit/
│   └── integration/
└── src/agents_rag/
    ├── __init__.py
    ├── config.py                  # pydantic-settings 配置
    ├── cli.py                     # Typer 入口（ingest/ask/eval）
    ├── pipeline/
    │   ├── ingest.py              # 索引管线编排
    │   └── query.py               # 查询管线编排
    ├── parsing/                   # base, docling, mineru, office, html, router
    ├── chunking/                  # base, structural, parent_child
    ├── indexing/                  # embedder, vectorstore(抽象), chroma_store, bm25_index
    ├── retrieval/                 # base, vector, bm25, hybrid(RRF), reranker
    ├── generation/                # llm, prompts, context_builder
    ├── citation/                  # sources（引用溯源与校验）
    └── eval/                      # ragas_eval
```

## 7. 核心数据结构（pydantic，不可变）

```python
class Document:
    id: str                 # 文档指纹（内容 hash）
    source: str             # 文件路径 / URL
    doc_type: str           # pdf | markdown | docx | xlsx | pptx | html | txt
    raw_text: str
    sections: list[Section] # 结构化章节（标题/段落/表格）
    metadata: dict

class Chunk:
    id: str
    doc_id: str
    text: str
    page: int | None
    heading: str | None
    parent_id: str | None   # 父子分块：父块 id
    embedding: list[float] | None

class RetrievalResult:
    chunk: Chunk
    score: float
    retriever: str          # vector | bm25 | fused | reranked

class Citation:
    doc_id: str
    source_name: str
    page: int | None
    snippet: str

class Answer:
    text: str
    citations: list[Citation]
    used_context_ids: list[str]
```

## 8. 关键接口（抽象 → 实现，便于替换）

| 接口 | 方法 | 实现 |
|------|------|------|
| `Parser` | `parse(file) -> Document` | Docling / MinerU / Office / HTML |
| `ParserRouter` | `route(file) -> Parser` | 按扩展名路由 + 诊断式降级（`poor_reason` 选兜底，只降一级） |
| `Chunker` | `chunk(doc) -> list[Chunk]` | 结构感知 + 父子分块 |
| `Embedder` | `embed(texts) -> list[Vec]` | embedding-3（默认智谱），批量 + 缓存 |
| `VectorStore` | `upsert / delete / query` | Chroma（抽象后可换 Qdrant） |
| `BM25Index` | `index / query` | rank_bm25 + jieba |
| `Retriever` | `retrieve(query, k)` | 向量 / BM25 |
| `HybridRetriever` | 向量 + BM25 → RRF 融合 | — |
| `Reranker` | `rerank(query, candidates) -> ranked` | Rerank API（默认智谱） |
| `ContextBuilder` | `build(results) -> context` | 去重 + token 预算 + 引用编号 |
| `Generator` | `generate(query, context) -> Answer` | GLM-4.5 |
| `CitationChecker` | `check(answer, results) -> Answer` | 校验引用编号命中 |

## 9. 数据流

### 索引管线（pipeline/ingest.py）
1. 扫描 `data/raw/`，对每个文件计算指纹；与已索引集合比对，跳过未变更、删除已移除
2. `ParserRouter` 选 parser → 解析为 `Document`（含 sections）
3. 清洗归一化（去多余空白、统一标点）
4. `Chunker` 结构感知分块 + 父子分块，附带 page/heading 元数据
5. `Embedder` 批量向量化（受并发上限约束，带本地缓存）
6. `VectorStore.upsert` 写 Chroma；`BM25Index.index` 建 BM25
7. 持久化文档指纹清单

### 查询管线（pipeline/query.py）
1. （可选）查询改写 / HyDE
2. 并行：`VectorRetriever` 取 `VECTOR_TOP_K`，`BM25Retriever` 取 `BM25_TOP_K`
3. `HybridRetriever` 用 RRF 融合
4. `Reranker.rerank` 取 `RERANK_TOP_N`
5. `ContextBuilder` 去重 + 按 token 预算截断 + 注入引用编号 `[1] [2]...`
6. `Generator.generate`（prompt 强制：仅基于上下文、必须引用编号、不知则说明）
7. `CitationChecker` 校验引用编号命中检索结果，剔除/标记无效引用
8. 返回 `Answer`（含 `citations`）

## 10. 配置（.env + pydantic-settings）

```dotenv
# .env.example（真实 .env 不入库）
LLM_API_KEY=
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
LLM_MODEL=glm-4.5
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIM=2048
EMBEDDING_MAX_BATCH=64
EMBEDDING_MAX_CONCURRENCY=8
RERANK_MODEL=rerank-2
CHUNK_SIZE=512
CHUNK_OVERLAP=64
VECTOR_TOP_K=20
BM25_TOP_K=20
RERANK_TOP_N=6
LLM_MAX_CONTEXT_TOKENS=6000
DATA_DIR=./data
STORAGE_DIR=./storage
```

## 11. 错误处理与稳定性

- **API 限流/超时**：tenacity 指数退避重试，区分可重试（429/5xx）与不可重试（鉴权/参数）错误
- **解析失败 / 诊断式降级**：docling 结果质量不达标时，按 `poor_reason` 选对应兜底（`scan`→`OCRParser` 强制图像 OCR；`layout`→MinerU 复杂版面），**只降一级**；兜底仍 poor 或无对应兜底（如 MinerU 未装）则跳过该文件并记录结构化日志，不中断批量
- **检索为空**：返回「未找到相关内容」，不硬编答案
- **引用校验失败**：生成的引用编号若未命中检索结果，剔除并标记
- **密钥缺失**：启动时校验 `LLM_API_KEY`，缺失即 fail-fast 报错

## 12. 测试与评估

- **单元测试**（纯逻辑，易测）：分块策略、RRF 融合、上下文构建、引用校验、文档指纹
- **集成测试**：小文档集端到端「索引 → 查询 → 引用」
- **评估**：构建小评测集（问答 + 标准答案 + 相关文档），用 RAGAS 跑 faithfulness / answer_relevancy / context_precision / context_recall，作为回归基线
- 覆盖率目标 ≥ 80%

## 13. 难点与应对

1. **文档解析（质量上限）**：Docling 主力（版面+表格结构化输出强）；**诊断式降级**——`assess_quality` 产出 `poor_reason`（`scan`/`layout`），router 按原因选 RapidOCR（扫描件）/ MinerU（复杂版面）兜底，只降一级；`ParserRouter` 屏蔽格式差异。
2. **分块策略**：结构感知分块（按标题/段落/表格单元，不切断语义）+ 父子分块（检索小块、回传带父块上下文）；chunk_size/overlap 可配置。
3. **检索质量**：向量 + BM25 双路召回（专有名词/型号/编号靠 BM25）→ RRF 融合 → Rerank 精排（默认智谱）。
4. **引用溯源与抗幻觉**：上下文片段带编号注入 prompt，强制引用；「无法确定」兜底；`CitationChecker` 后处理校验。
5. **上下文构建**：去重（混合检索必重复）+ token 预算截断 + Rerank 分数过滤。
6. **中文特性**：jieba 分词供 BM25；按中文标点/段落分块；embedding-3 中文友好。
7. **评估体系**：RAGAS 自动回归 + 人工评测集。
8. **增量更新**：文档指纹 hash + 向量库 upsert + BM25 重建/增量。
9. **工程化**：embedding 缓存与批处理、异步并发、指数退避重试、密钥与配置分离、结构化日志。
10. **扩展性**：抽象 `VectorStore` 接口，Chroma 起步，规模化切 Qdrant。

## 14. 依赖（pyproject.toml 核心）

```toml
[project]
name = "agents-rag"
requires-python = ">=3.12"
dependencies = [
    "openai",
    "chromadb",
    "rank-bm25",
    "jieba",
    "docling",
    "mineru",                 # 复杂 PDF 兜底（安装较重，按需）
    "python-docx", "openpyxl", "python-pptx",
    "trafilatura", "beautifulsoup4", "lxml",
    "rapidocr-onnxruntime",
    "pydantic", "pydantic-settings",
    "typer", "rich",
    "tenacity",
    "ragas", "datasets",
]

[project.optional-dependencies]
api = ["fastapi", "uvicorn"]
ui = ["streamlit"]
dev = ["pytest", "pytest-asyncio", "ruff"]
```

> 注：`mineru` 依赖较重（含模型），若首版 PDF 复杂度不高可延后引入，先用 Docling + OCR。

## 15. MVP 实现顺序（自底向上，每层带单元测试）

按依赖顺序逐步实现，每层完成即写测试：

1. `config.py` + `.env.example`（配置与密钥骨架）
2. `parsing/`（base → docling → office → html → router；router 诊断式降级：scan→OCR、layout→MinerU）
3. `chunking/`（structural → parent_child）
4. `indexing/embedder`（embedding-3，默认智谱，批量+缓存）
5. `indexing/vectorstore + chroma_store`、`indexing/bm25_index`
6. `retrieval/`（vector → bm25 → hybrid/RRF → reranker）
7. `generation/`（llm → context_builder → prompts）
8. `citation/`（sources + 校验）
9. `pipeline/ingest + query`（编排）
10. `cli.py`（ingest / ask / eval 子命令）
11. 小评测集 + RAGAS 评估回归

## 16. 后续路线

- **API 阶段**：FastAPI 封装查询管线（`/ask`、`/ingest`），加并发与鉴权
- **UI 阶段**：Streamlit 对话界面，展示回答 + 引用高亮 + 来源跳转
- **规模化**：`VectorStore` 切换 Qdrant；引入查询路由 / 多路召回扩展

## 参考资料

- [智谱 AI 开放平台 BigModel](https://bigmodel.cn/)
- [智谱 Embedding-3 文档](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3)
- [智谱 文本重排序（Rerank）](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E6%96%87%E6%9C%AC%E9%87%8D%E6%8E%92%E5%BA%8F)
- [智谱 新品发布](https://docs.bigmodel.cn/cn/update/new-releases)
