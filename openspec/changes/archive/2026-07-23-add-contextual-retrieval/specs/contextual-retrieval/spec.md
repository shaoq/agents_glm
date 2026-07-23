## ADDED Requirements

### Requirement: chunk 上下文前缀生成
系统 SHALL 支持为每个子块（ChildChunk）用便宜 LLM 生成客观定位前缀（context，50-100 字），描述该 chunk 在文档中的位置与主题；context SHALL 仅陈述客观信息，不含概括或评价。功能默认关闭（`contextualization_enabled=False`），需显式启用。

#### Scenario: CR 启用生成 context
- **WHEN** `contextualization_enabled=True` 且 ingest 新文档
- **THEN** 每个 child chunk 获得非空 context（客观定位前缀）

#### Scenario: CR 关闭行为不变
- **WHEN** `contextualization_enabled=False`
- **THEN** child.context="" 且 indexed_text=text，与无 CR 时行为一致

### Requirement: indexed_text 检索/回传分离
系统 SHALL 定义 `indexed_text = context + "\n\n" + text`（context 非空时）或 `indexed_text = text`（context 空时）；embedding 与 BM25 SHALL 使用 `indexed_text`（检索用），Chroma document SHALL 存原文 `text`（回传用）。

#### Scenario: 检索用 indexed_text
- **WHEN** CR 启用且 chunk 有 context
- **THEN** embedding 输入 = indexed_text（context+chunk），BM25 分词 = indexed_text

#### Scenario: 回传用原文
- **WHEN** 查询侧读 Chroma document
- **THEN** 返回原文 text（不含 context 前缀）

### Requirement: context 缓存增量复用
系统 SHALL 以 `text_fingerprint(chunk.text) + context_model` 为键缓存 context；chunk 文本与 context 模型均不变时命中缓存、不调用 LLM。

#### Scenario: chunk 不变命中缓存
- **WHEN** 文档更新但某 chunk 文本未变
- **THEN** context 缓存命中，不调用 context LLM

#### Scenario: 换 context 模型失效旧缓存
- **WHEN** `contextualization_model` 变更
- **THEN** 旧 context 缓存不命中，重新生成

### Requirement: 失败兜底
系统 SHALL 在 context 生成失败时返回空串（context=""），使 indexed_text 退化为原文 text，不中断索引流程。

#### Scenario: LLM 失败兜底
- **WHEN** context LLM 调用失败
- **THEN** context="" → indexed_text=text，chunk 正常索引（退化为无 CR）
