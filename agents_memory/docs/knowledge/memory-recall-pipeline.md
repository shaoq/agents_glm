# Agent Memory 知识点：记忆召回管线（Memory Recall Pipeline）

> **文档定位**：本文聚焦 Agent Memory 的**召回管线**——如何从记忆库中高效召回相关记忆，注入 Agent 的推理上下文。
>
> **与 RAG 查询管线的区别**：
> - RAG 检索是「query vs 文档」的语义匹配
> - Memory 召回是「当前 agent 状态 vs 个人经验」的多信号匹配（相关性 + 时近性 + 重要性）
>
> **深入进度**：⏳ 大纲，随讨论逐环节深入
>
> **更新日期**：2026-07-24

---

## 0. 核心认知

记忆召回管线的本质，是回答「**在当前上下文下，哪些过往记忆最相关**」。三个关键判断：

1. **相关性不是唯一维度**：仅按语义相似度召回不够——时间久远的记忆可能过时、不重要的记忆可能是噪音。
2. **三信号加权是业界标准**：Recency（时近性）+ Importance（重要性）+ Relevance（语义相关性）的加权融合。
3. **召回 vs RAG 检索的职责分离**：Memory 召回个人经验（注入 system prompt），RAG 检索文档知识（注入 context），两者并行不冲突。

---

## 1. 全景：召回管线的 4 个环节

```
当前 query/上下文 → ①Embedder → ②向量召回 + metadata 过滤
                 → ③三信号加权打分(Recency + Importance + Relevance)
                 → ④Top-K 返回 + 注入上下文
```

---

## 2. 向量召回（Vector Retrieval）

> ⏳ 待深入

- 本质：语义相似度检索（复用 agents_rag 的 embedding + Chroma）
- metadata 过滤（user_id / agent_id / session_id / memory_type）
- 与 RAG 向量召回的区别（同库不同 collection vs 独立库）

---

## 3. 三信号加权打分（Three-Signal Scoring）⭐⭐

> ⏳ 待深入

- **Relevance（相关性）**：query vs memory 向量余弦相似度
- **Recency（时近性）**：指数衰减 `0.99^(小时差)` → 最近记忆权重高
- **Importance（重要性）**：写入时 LLM 打分（1-10）→ 核心记忆权重高
- 加权公式：`score = α·Recency + β·Importance + γ·Relevance`
- 权重调参（Stanford 默认 α=β=γ=1；agents_memory 初版可调）
- vs RAG 的纯相关性检索（Memory 多了时近性 + 重要性两个维度）

---

## 4. Top-K 返回与上下文注入

> ⏳ 待深入

- Top-K 截断（默认 10）
- 注入 system prompt 的格式（「关于用户的已知信息：...」）
- 与 RAG context 的协同（Memory 先注入 → RAG 后注入 → LLM 生成）
- token 预算（Memory context + RAG context 不超 LLM 限制）

---

## 5. 召回管线常见陷阱

> ⏳ 待深入

---

## 6. agents_memory 落地

> ⏳ 待深入

---
