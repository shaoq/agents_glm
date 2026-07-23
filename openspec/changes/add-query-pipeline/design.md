## Context

知识构建侧已交付完整索引能力（双索引 + 父子 KV + 元数据 + 图片）。查询管线是 RAG 的在线闭环——消费已建索引，把用户问题变成带引用的可信回答。

关键事实（影响设计）：
- ChromaStore.query 返回 **distance**（小=近），BM25Index.query 返回 **score**（大=好）→ RRF 只用 rank 规避量纲
- BM25Index.query 无 `where` 参数（status 过滤只能在向量路；构建侧 update 已物理删旧，BM25 无脏数据）
- AutoMerging 需 (doc_id, parent_id) 二元组从子块 metadata 取
- 查询笔记 §2-§8 已深入讨论每环节的首版/后置取舍

## Goals / Non-Goals

**Goals:**
- 向量+BM25 双路召回 + RRF 融合 + 智谱 Rerank 精排
- AutoMerging（rerank 后父子回传）
- ContextBuilder（hash+父子去重 + token 预算 + 引用编号注入）
- GLM-4.5 生成（prompt 四约束：Grounding / 强制引用 / 先结论 / 兜底）
- CitationChecker（编号校验 + 剔除无效）
- 检索空兜底（不生成，省 LLM）
- `agents-rag ask` CLI

**Non-Goals:**
- 查询改写 / HyDE / Multi-Query（笔记 §2，首版 query 直接 embed）
- RAGAS 评估（需评测集 + ragas 依赖）
- 置信度聚合拒答（仅检索空兜底）
- faithfulness / 内容一致性校验（§7.4-7.5）
- Self-Check 修正循环 / 结构化输出 / 流式 / 多轮

## Decisions

**1. RRF 用 rank 不用 score（规避量纲）。**
Chroma distance（小=近）与 BM25 score（大=好）量纲相反。RRF `1/(k+rank)` 只用排名，绕开归一化/调权，鲁棒（笔记 §3.3）。k=60 经验值。

**2. AutoMerging 在 Rerank 之后。**
检索/RRF/Rerank 全在子块（检索单元），AutoMerging 在精排后按 parent_id 分组回传父块（回传单元）。不能在 RRF 前换父块（粒度不一致，融合失败）。merge_threshold=2（笔记 §3.5/§4.5）。

**3. 三方引用编号契约。**
`prompts.py` 定义编号格式常量（`[N]`），`context_builder` 注入编号 + `CitationChecker` 正则提取 `\[(\d+)\]` 引用同一常量。改一方同步三方（笔记 §5.5）。

**4. 检索空兜底在 RRF 后判断（不生成）。**
RRF 融合后无结果 → 直接返回 NO_RESULT（不走 Rerank/生成，省成本）。这是早期拒答（笔记 §6.4）。

**5. token 预算用 tiktoken + 15% buffer。**
tiktoken 与 GLM tokenizer 有 5-15% 偏差，buffer 补偿。`LLM_MAX_CONTEXT_TOKENS=6000`（保守，留足生成空间）（笔记 §5.4）。

**6. Generator/Reranker 复用 vision_describer 的 retry 客户端模式。**
tenacity 指数退避 + `_NonRetryable`（鉴权不重试），与 Embedder/VisionDescriber 一致。

**7. 接口全抽象（Retriever/Reranker/Generator 均为 ABC）。**
测试用 Fake 实现，不依赖密钥/网络。参照构建侧 Embedder/VectorStore 抽象模式。

## Risks / Trade-offs

- **[distance vs score 方向]** → RRF 只用 rank 规避（不做归一化加权）
- **[BM25 无 status 过滤]** → 构建侧 update 物理删旧保证无脏数据；若未来改 superseded 标记需补 post-filter
- **[tiktoken 偏差]** → 15% buffer 补偿；核实智谱 SDK 是否有 token counting 后升级
- **[Rerank API 核实]** → RERANK_MODEL=rerank-2 待按官方文档核实（输入 query + candidates，返回分数排序）
- **[AutoMerging token 压力]** → 大父块（~1800 字）+ 子块混合，token 预算截断时高分优先
- **[CitationChecker 只校验格式]** → 编号命中 ≠ 内容支撑（faithfulness 后置）
