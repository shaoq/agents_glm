## MODIFIED Requirements

### Requirement: 统一文档模型
系统 SHALL 将所有支持格式的文档解析为统一的 `Document` 结构：含 `doc_id` / `source` / `doc_type` 与按**标题层级组织为嵌套树**的 `sections`；每个 section 含类型化的 `blocks`（paragraph / table / list / code / **image**）并保留位置元数据（`page` / `bbox`）；子标题 SHALL 作为父标题 section 的 `children`（而非平级 section），同一标题下的段落 / 表格 / 图片 SHALL 聚合进该 section 的 `blocks`。具备标题层级识别能力的解析器（PDF / Markdown / HTML）SHALL 产出嵌套树；无标题层级的解析器（Office）产出单 section。**图片（picture）SHALL 产出 `IMAGE` block（携带 `image_ref` / `caption` / `page`，`text` 为图片描述），而非丢弃。**

#### Scenario: PDF 解析为嵌套章节树
- **WHEN** 解析一个含多级标题与表格的 PDF（如 H1 > H2）
- **THEN** 产出 `Document`，其顶层 sections 为 H1，H2 作为 H1 的 `children`；表格为 `type=table` 的 block 且归属其所在标题的 section

#### Scenario: Markdown 解析为嵌套标题树
- **WHEN** 解析一个含 `#` / `##` / `###` 的 Markdown
- **THEN** 产出 `Document`，sections 按标题层级嵌套（`##` 是 `#` 的 children，`###` 是 `##` 的 children），同一标题下的段落聚合进该 section 的 `blocks`

#### Scenario: section_path 反映完整层级
- **WHEN** 对嵌套 sections 进行结构感知分块
- **THEN** 产出的父块 `section_path` 反映完整标题路径（如 `H1 > H2 > H3`），而非仅单层标题

#### Scenario: 无标题层级文档退化为单 section
- **WHEN** 解析无标题层级的文档（如 Office docx / xlsx / pptx）
- **THEN** 产出 `Document`，其 sections 为单层（无 `children` 嵌套），`iter_blocks` 仍能遍历全部 block

#### Scenario: 图片产出 IMAGE block
- **WHEN** 解析一个含图片的 PDF
- **THEN** 产出 `IMAGE` block（`type=image`），携带 `image_ref`（原图存储引用）/ `caption` / `page`，`text` 为可向量化的图片描述；图片不再被丢弃
