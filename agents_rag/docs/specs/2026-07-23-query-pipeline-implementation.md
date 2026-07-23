# agents_rag 查询管线核心闭环 · 实施设计

| 项 | 值 |
|----|----|
| 日期 | 2026-07-23 |
| 状态 | 已批准，待实现 |
| 范围 | 查询管线（在线问答）核心闭环 |
| 运行环境 | conda 环境 `agents_glm`（Python 3.12.13） |
| 上游文档 | [整体设计 spec](./2026-07-21-agents-rag-design.md) · [查询管线知识笔记](../knowledge/rag-query-pipeline.md) · [构建管线实施设计](./2026-07-22-knowledge-base-core-implementation.md) |

---

## 0. 文档定位

本文是 [整体设计 spec](./2026-07-21-agents-rag-design.md) §15「MVP 实现顺序」步骤 6-10 的**实施细化**，聚焦查询管线核心闭环：检索 → 生成 → 引用。

- 讲「本轮具体做什么、目录怎么落、接口怎么定、做到哪停」
- 原理与方案取舍见 [查询管线知识笔记](../knowledge/rag-query-pipeline.md) §2-§8（本文引用其小节，不重复）

**本轮不做**：查询改写/HyDE（首版 query 直接 embed）、RAGAS 评估（需评测集）、置信度聚合拒答（仅检索空兜底）、多轮对话、faithfulness 二次校验。

---

## 1. 首版范围（核心闭环）

查询管线 7 环节中，本轮实现**主线全链路**（检索→重排→上下文→生成→引用校验）：

| 环节 | 首版 | 笔记依据 | 后置 |
|------|------|---------|------|
| 查询改写/HyDE | ⏳ 不做 | §2 | 首版 query 直接 embed（§2 结论） |
| 检索（向量+BM25+RRF） | ✅ | §3 | — |
| Rerank（智谱 API） | ✅ | §4 | — |
| AutoMerging（父子回传） | ✅ | §3.5 | — |
| 上下文构建（去重+预算+编号） | ✅ | §5 | MMR / 动态上下文 |
| 生成（GLM-4.5 四约束） | ✅ | §6 | Self-Check / 流式 / JSON mode |
| 引用校验（CitationChecker） | ✅ | §7 | faithfulness / 内容一致性 |
| 置信度拒答 | ⏳ 仅检索空兜底 | §6.4 | 多信号聚合后置 |
| RAGAS 评估 | ⏳ | §8 | 需评测集 + ragas 依赖 |
| 多轮对话 | ⏳ | §6.5 | 首版单轮 |

---

## 2. 目录结构（查询侧新建）

在现有 `src/agents_rag/` 基础上新增查询侧模块（构建侧不动）：

```
src/agents_rag/
├── retrieval/                    # 查询侧新建
│   ├── __init__.py
│   ├── base.py                   # Retriever ABC + RetrievalResult
│   ├── vector.py                 # VectorRetriever（embed query → ChromaStore.query）
│   ├── bm25.py                   # BM25Retriever（BM25Index.query）
│   ├── hybrid.py                 # HybridRetriever（RRF 融合，k=60）
│   └── reranker.py               # Reranker ABC + 智谱 API 实现
├── generation/                   # 查询侧新建
│   ├── __init__.py
│   ├── prompts.py                # system prompt 四约束 + 引用编号格式常量（三方契约）
│   ├── context_builder.py        # ContextBuilder（hash+父子去重 + token 预算 + 编号注入）
│   └── llm.py                    # Generator ABC + GLM 实现（OpenAI 兼容 chat completions）
├── citation/                     # 查询侧新建
│   ├── __init__.py
│   ├── sources.py                # Citation 模型 + 从 metadata 构造引用
│   └── checker.py                # CitationChecker（正则提取 + 集合比对 + 剔除无效）
├── pipeline/
│   ├── ingest.py                 # 已有（索引管线）
│   └── query.py                  # 查询侧新建：QueryPipeline 编排
├── models.py                     # 补：RetrievalResult / Citation / Answer
├── config.py                     # 补：6 个查询参数
└── cli.py                        # 补：ask 子命令
```

---

## 3. 核心数据结构（pydantic frozen，补到 models.py）

```python
# —— 检索结果（查询侧）——
class RetrievalResult:
    chunk_id: str
    text: str
    score: float                  # rerank 分数或 RRF 分数（排名后）
    retriever: str                # vector | bm25 | fused | reranked
    doc_id: str                   # 从 chunk metadata 取
    parent_id: str
    page: int | None
    heading: str | None
    section_path: str
    source_name: str              # 文档名（从 registry/doc_type 推导）
    image_ref: str | None         # 图片块关联原图

# —— 引用（溯源展示用）——
class Citation:
    doc_id: str
    source_name: str              # 文档名
    page: int | None
    snippet: str                  # 原文片段（截断）

# —— 查询回答 ——
class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    NO_RESULT = "no_result"

class Answer:
    text: str                     # 生成回答（带 [N] 引用标注）
    citations: list[Citation]     # 有效引用列表
    used_context_ids: list[str]   # 使用的 chunk_id
    status: AnswerStatus          # answered | no_result
    message: str                  # no_result 时的提示消息
```

> 现有可复用：`ChildChunk.metadata_dict()` 提供 doc_id/parent_id/page/heading/section_path/image_ref；`ParentChunk` 提供 text/page/heading。

---

## 4. 关键接口（抽象 → 实现，复用现有）

| 接口 | 方法 | 本轮实现 | 复用现有 |
|------|------|---------|---------|
| `Retriever`(ABC) | `retrieve(query_vec_or_text, k) → list[RetrievalResult]` | VectorRetriever / BM25Retriever | `Embedder.embed` + `ChromaStore.query` / `BM25Index.query` |
| `HybridRetriever` | `retrieve(query, k) → list[RetrievalResult]` | RRF 融合（k=60，用 rank 不用 score） | 消费两路 Retriever |
| `Reranker`(ABC) | `rerank(query, candidates) → list[RetrievalResult]` | 智谱 Rerank API（OpenAI 兼容） | tenacity + `_NonRetryable` 模式（复用 `embedder.py`） |
| `ContextBuilder` | `build(results, query) → (context_str, id_map)` | hash+父子去重 + token 截断 + `[N]（文档名,页码）` 注入 | `text_fingerprint` + `ParentStore.get` |
| `Generator`(ABC) | `generate(query, context) → str` | GLM-4.5（OpenAI 兼容 chat completions） | retry 客户端模式（复用 `vision_describer.py`） |
| `CitationChecker` | `check(answer_text, context_ids) → Answer` | 正则 `\[(\d+)\]` + 集合比对 + 剔除无效 | 纯 Python |

### 关键衔接点

1. **distance vs score 方向相反**：`ChromaStore.query` 返回 distance（小=近），`BM25Index.query` 返回 score（大=好）。RRF 只用 rank（不用 score）规避量纲问题（笔记 §3.3）。
2. **status pre-filter**：向量路 `where={"status": "active"}`；BM25 无 `where` 参数（构建侧 update 已物理删旧，BM25 无脏数据）。
3. **AutoMerging 需 (doc_id, parent_id) 二元组**：从子块 metadata 取两字段 → `ParentStore.get(doc_id, parent_id)`。
4. **三方引用编号契约**：`prompts.py` 定义编号格式常量 → `context_builder` 注入 + `CitationChecker` 解析引用同一常量。
5. **token 预算**：tiktoken + 15% buffer（GLM tokenizer 偏差补偿，笔记 §5.4）。

---

## 5. 查询管线编排（pipeline/query.py）

笔记 §3.5 / §4.5 的时序落地：

```
QueryPipeline.ask(query: str) → Answer:

1. embed(query) → query_vec                       # Embedder.embed([query])[0]
2. VectorRetriever.retrieve(query_vec, k=20)       # ChromaStore.query(where={"status":"active"})
   BM25Retriever.retrieve(query_text, k=20)         # BM25Index.query（load 后）
3. HybridRetriever RRF 融合 → 统一排序              # k=60，rank 倒数求和
4. Reranker.rerank(query, candidates, top_n=6)     # 智谱 Rerank API
5. AutoMerging(reranked, parent_store, threshold=2) # 按 parent_id 分组，≥threshold 回传父块
6. ContextBuilder.build(merged_results, query)      # 去重(hash+父子) + token 预算 + [N] 编号注入
7. Generator.generate(query, context_str)           # GLM-4.5 四约束（Grounding/引用/先结论/兜底）
8. CitationChecker.check(answer_text, context_ids)  # 正则提取 [N] → 集合比对 → 剔除无效 + 构造 Citation

if 检索为空（步骤 2-3 无结果）:
    → 直接返回 Answer(status=NO_RESULT, message="未找到相关内容")  # 不生成（省 LLM）
else:
    → 返回 Answer(status=ANSWERED, text, citations, used_context_ids)
```

要点：
- **检索空兜底**在步骤 3 后判断（RRF 融合后无结果 → 不走 4-8，省 Rerank + 生成成本）
- **AutoMerging 在 Rerank 后**（笔记 §3.5/§4.5 确认：检索/RRF/Rerank 全在子块上，精排后换父块）
- **Generator 复用 `vision_describer.py` 的 retry 客户端模式**（tenacity 指数退避 + `_NonRetryable` 鉴权不重试）
- **构造风格**参照 `IngestPipeline`：关键字参数注入所有组件，便于测试用 Fake 替换

---

## 6. 实现顺序（自底向上，每层带单元测试）

1. `models.py` 补 `RetrievalResult` / `Citation` / `AnswerStatus` / `Answer`
2. `config.py` + `.env.example` 补 6 个查询参数
3. `pyproject.toml` 加 `tiktoken` 依赖
4. `retrieval/`：`base`(Retriever ABC) → `vector` → `bm25` → `hybrid`(RRF) → `reranker`(智谱 API)
5. `generation/`：`prompts`(四约束+常量) → `context_builder` → `llm`(Generator)
6. `citation/`：`sources`(Citation 构造) → `checker`(CitationChecker)
7. `pipeline/query.py`：`QueryPipeline` 编排（串联 4+5+6）
8. `cli.py` 加 `ask` 子命令
9. 测试补齐 + 端到端（真实密钥 `agents-rag ask`）

---

## 7. 配置（.env + pydantic-settings，补到现有）

```dotenv
# 查询管线参数（补到 .env.example）
LLM_MODEL=glm-4.5
RERANK_MODEL=rerank-2
VECTOR_TOP_K=20
BM25_TOP_K=20
RERANK_TOP_N=6
LLM_MAX_CONTEXT_TOKENS=6000
```

- 复用现有：`LLM_API_KEY` / `LLM_BASE_URL`（OpenAI 兼容端点）、`EMBEDDING_*`（query embedding）
- `config.py` 的 `Settings` 类补对应字段（+ `.env.example` + `.env` 同步，遵循 CLAUDE.md 规则）

---

## 8. 依赖（pyproject.toml 新增）

```toml
# 补到 [project] dependencies
"tiktoken>=0.7",  # ContextBuilder token 预算（+15% buffer 补偿 GLM tokenizer 偏差）
```

- 复用：`openai>=1`（GLM 生成 + Rerank 走 OpenAI 兼容 chat/rerank 接口）
- 后置：`ragas` / `datasets`（RAGAS 评测，§8）

---

## 9. 测试策略

覆盖率目标 ≥ 80%。

- **单元测试（纯逻辑，离线）**：
  - `hybrid` RRF 融合（排名倒数求和、两路对齐）
  - `context_builder` 去重（hash+父子）、token 预算截断、编号注入格式
  - `checker` CitationChecker（正则提取、集合比对、错标剔除、漏标检测）
  - `prompts` 四约束模板完整性
- **集成测试（Fake 组件）**：
  - `FakeEmbedder` + `FakeReranker` + `FakeGenerator` + tmp_path 索引
  - 端到端 `QueryPipeline.ask` → 断言 Answer 结构 + 引用命中
  - 检索空 → status=NO_RESULT 兜底
- **端到端（真实密钥）**：
  - 先 `agents-rag ingest tests/fixtures`（建索引）
  - 再 `agents-rag ask "GLM-4.5 支持多少维 embedding？"` → 验证带引用回答

> 接口抽象使单测不依赖密钥：`Retriever` / `Reranker` / `Generator` 均为 ABC，测试用 Fake 实现。

---

## 10. 后置清单（本轮明确不做）

- 查询改写 / HyDE / Multi-Query / 子问题分解（笔记 §2）
- RAGAS 评估 + 评测集构建（笔记 §8）
- 置信度聚合拒答（多信号加权 + 阈值；笔记 §6.4，本轮仅检索空兜底）
- faithfulness 二次校验 + 内容一致性校验（NLI/judge；笔记 §7.4-7.5）
- Self-Check 修正循环（生成→校验→反馈修正→再校验；笔记 §6.5）
- 结构化输出（JSON mode / function calling；笔记 §6.5）
- 流式生成（笔记 §6.5）
- 多轮对话 + 历史压缩（笔记 §6.5）
- 多模型协作（Flash 辅助；笔记 §6.5）
- lost-in-the-middle 排序优化（头尾高分；笔记 §5.3）
- MMR 多样性去重（笔记 §5.7）

---

## 11. 验收标准

- [ ] `agents-rag ask "问题"` 跑通，返回带 `[N]` 引用标注的回答
- [ ] 检索（向量+BM25+RRF）正确消费已建索引（需先 ingest）
- [ ] Rerank 精排（智谱 API）缩小候选到 top_n=6
- [ ] AutoMerging 父子回传（≥threshold 回传父块）
- [ ] 生成（GLM-4.5 四约束：Grounding / 强制引用 / 先结论 / 兜底）
- [ ] CitationChecker 校验引用编号命中（剔除无效）
- [ ] 检索空兜底 → status=NO_RESULT + message
- [ ] 单测覆盖率 ≥ 80%，全绿
- [ ] 真实密钥端到端：`agents-rag ask` 对 fixtures 文档提问成功
