## Context

查询管线基线已闭环（检索→RRF→Rerank→AutoMerging→ContextBuilder→Generator→CitationChecker→Faithfulness）。第①步 `embed(query)` 直接用原始 query 检索，无改写。

笔记 §2 系统论述查询改写：原始 query 常非最佳检索 query（口语化 / 表述差异 / 术语不齐 → recall 打折）。方案谱：查询改写 / Multi-Query / 子问题分解 / HyDE / Step-Back。在领域/企业文档场景，**查询改写稳**（对齐术语），**HyDE 危险**（领域术语偏差致命，§2.3 反直觉点 / §2.7 陷阱）。

现有基建可直接复用：
- 便宜 LLM `GLM-4.7-Flash` 已用于 contextualization（索引侧）+ faithfulness（查询侧）
- `citation/faithfulness.py` 提供成熟 retry 客户端模式（OpenAI + tenacity + `_NonRetryable`）
- `retrieval/hybrid.py` 的 `rrf_fuse` 可复用做双 query 融合

CR 对称性：已上线的 Contextual Retrieval 是"文档侧"对齐（chunk 加客观定位前缀）；查询改写是"query 侧"对齐（query 补术语/消口语）。两者都缓解 query↔文档表征不对等。

## Goals / Non-Goals

**Goals:**
- `QueryRewriter`（Flash）把口语/模糊 query 改写为检索友好（不编答案）
- 双 query RRF 融合（复用 `rrf_fuse`），原 query + 改写 query 都召回
- 触发判据靠双 query RRF 绕过（opt-in 全改写，RRF 兜底）
- 失败兜底（`None` / `rewritten == query` → 回退原 query 单路，不阻塞）
- opt-in 默认关，为 eval 备"有改写 vs 无改写"实验组

**Non-Goals:**
- 触发判据路由（规则 / LLM 判断要不要改写）
- Multi-Query（N 个变体分别检索）
- HyDE（领域文档危险，不做）
- 子问题分解 / Step-Back
- 指代消解 / 多轮历史改写（首版无多轮）
- 改写结果缓存（query 长尾命中率低）
- 收益验证（依赖后置 eval 闭环）

## Decisions

**1. 改写器结构：照搬 `faithfulness.py`。** OpenAI client + tenacity `@retry` + `_NonRetryable`（鉴权不重试）。零新基建，与 faithfulness / reranker 一致的稳定性模式。

**2. 改写 ≠ HyDE：只换说法，不编答案。** prompt 明确"不回答问题，只改写检索查询"。规避 HyDE 在领域文档的术语偏差致命点（§2.3：假设答案事实可错但术语不能错，而领域恰恰术语最易错）。

**3. 双 query 完整 RRF（而非"原 query 喂向量 + 改写 query 喂 BM25"）。** 原 query + 改写 query 各跑完整 `HybridRetriever`（vector+BM25+RRF），再 `rrf_fuse` 融合两组结果。
- 备选（否决）：原 query→向量路、改写 query→BM25 路。看似省一半检索，但切断每路内部 vector+BM25 互补。
- 代价：2× 检索（检索 cheap，相对 LLM 可接受）。
- 红利：隐式缓解下游耦合（§2.5D）——原 query 自然语言向量强、改写 query 关键词式 BM25 强，各跑双路后强项在 RRF 占优。
- **`rrf_fuse` 正名为通用 N 路**：签名从 `(vector_results, bm25_results)` 改为 `*rankings`，脱开 vector/bm25 命名绑定（实现本就对称通用）。双 query 场景 `rrf_fuse(original, rewritten)` 嵌套 2 路融合——名副其实（两个"已融合的 query 视角"），不再误导成 vector/bm25。

**4. 触发判据靠双 query RRF 绕过。** 不判断要不要改写，opt-in 全改写；改写无用时 RRF 自动稀释其排名，不伤害。规避 §2.5A（最关键、最易踩坑点）。

**5. 失败兜底：改写是"锦上添花"，永不阻塞在线查询。** 异常 → 返回 `None`；`rewritten == query`（LLM 判定已规范）→ 原样。两者都回退原 query 单路 `HybridRetriever`。与 reranker 失败兜底（`reranker.py:97` 用原序）一脉相承。

**6. 插入点：`QueryPipeline.ask` 第①步（embed 前）。** 改写产物供后续 embed + 双路检索；下游 Rerank 起完全不变。

**7. 改写 prompt 5 规则（对齐 `generation/prompts.py` 风格）。** 去口语化 / 补术语 / 保留原意不发散不答 / 简洁关键词式（喂 BM25 胃口，缓解 §2.5D）/ 已规范不改（防过度改写丢原意）。

**8. 配置 opt-in。** `query_rewrite_enabled`(False) + `query_rewrite_model`(GLM-4.7-Flash)。默认关，行为不变；config 开关为 eval A/B 提供零成本切换。

## Risks / Trade-offs

- **[改写发散 / 丢原意]** → prompt 规则 3/5 约束 + 双 query RRF 兜底（原 query 总在融合池中）
- **[+1 LLM 延迟]** → Flash 便宜快；opt-in；失败回退原路
- **[2× 检索成本]** → 检索 cheap（相对 LLM）；仅 opt-in；`rewritten == query` 时省回（走原路单次检索）
- **[收益未验证]** → 默认关；定位为 eval 实验组，非正式生效；待 recall@k 评测集量化后决定去留
- **[改写 prompt 质量不定]** → 首版经验 prompt，待 eval 后调；可后续 A/B 多版 prompt
