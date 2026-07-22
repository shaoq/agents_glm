## ADDED Requirements

### Requirement: 结构感知分块
系统 SHALL 基于 `Document` 的章节树进行结构感知分块：切断只发生在 block 之间，MUST NOT 在段落 / 句子内部切断；每个 chunk SHALL 继承其所在章节路径（`section_path`）。

#### Scenario: 不在句子内部切断
- **WHEN** 对一个文档分块
- **THEN** 任何 chunk 都不在句子中间被截断

### Requirement: 特殊结构豁免
系统 SHALL 将表格 / 代码 / 列表作为原子块整体保留，MUST NOT 在其内部切断。

#### Scenario: 表格整体保留
- **WHEN** 文档含表格
- **THEN** 表格作为一个完整 chunk，行列关系不被破坏

### Requirement: 父子分块
系统 SHALL 产出父子分块：父块（按 section 切分，受 `parent_max_size` 约束）存入父块 KV 且不建向量索引；子块（由父块按 `chunk_size` / `overlap` 切出）建向量与 BM25 索引；子块 id SHALL 编码其 `parent_id`。

#### Scenario: 子块 id 编码父块
- **WHEN** 切出一个父块下的第 i 个子块
- **THEN** 该子块 id 形如 `<parent_id>__<i>` 且 `parent_id` 指向所属父块

#### Scenario: 父块不建向量索引
- **WHEN** 分块完成
- **THEN** 父块仅写入父块 KV，不进入向量库

### Requirement: 溯源元数据
每个子块 SHALL 携带完整溯源元数据：`doc_id` / `page` / `heading` / `section_path` / `parent_id` / `block_type` / `char_span` / `version` / `status`。

#### Scenario: chunk 元数据完整可溯源
- **WHEN** 取任一子块
- **THEN** 其元数据足以回溯到「文档名 + 页码 + 章节」
