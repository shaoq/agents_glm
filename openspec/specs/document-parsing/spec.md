# document-parsing Specification

## Purpose
TBD - created by archiving change add-knowledge-base-core. Update Purpose after archive.
## Requirements
### Requirement: 统一文档模型
系统 SHALL 将所有支持格式的文档解析为统一的 `Document` 结构：含 `doc_id` / `source` / `doc_type` 与按**标题层级组织为嵌套树**的 `sections`；每个 section 含类型化的 `blocks`（paragraph / table / list / code）并保留位置元数据（`page` / `bbox`）；子标题 SHALL 作为父标题 section 的 `children`（而非平级 section），同一标题下的段落 / 表格 SHALL 聚合进该 section 的 `blocks`。具备标题层级识别能力的解析器（PDF / Markdown / HTML）SHALL 产出嵌套树；无标题层级的解析器（Office）产出单 section。

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

### Requirement: 多格式解析
系统 SHALL 按文件扩展名路由到对应解析器，支持 PDF、Markdown、HTML、Office（docx / xlsx / pptx）。

#### Scenario: 按扩展名路由
- **WHEN** 输入 `report.pdf`
- **THEN** 系统选用 docling 解析器；输入 `note.md` 则选用 markdown 解析器

### Requirement: 解析路由降级链
系统 SHALL 通过 `ParserRouter` 提供降级链：主解析器结果质量不达标时降级到备选；所有解析器均失败时跳过该文件并记录日志，MUST NOT 中断批量索引。

#### Scenario: 文本过少触发低质量标记
- **WHEN** 解析结果每页平均字符数低于阈值
- **THEN** 该结果被判定为低质量（可能为扫描件）

#### Scenario: 全部解析失败时跳过不中断
- **WHEN** 某文件所有解析器均失败
- **THEN** 系统跳过该文件、记录日志，且批量索引继续处理其他文件

### Requirement: 解析质量评估
系统 SHALL 对解析结果产出质量报告（至少含每页平均字符数、表格数、乱码占比等信号），作为降级判据。

#### Scenario: 扫描件被识别
- **WHEN** 解析一个扫描件 PDF
- **THEN** 质量报告的每页平均字符数异常低

### Requirement: 清洗归一化
系统 SHALL 在解析后、分块前执行轻量清洗归一化（空白规整、全半角统一、去页眉页脚），按 `doc_type` 可配置；清洗 MUST NOT 破坏结构（标题层级 / 表格 / 列表），且 MUST 保留 `page` / `heading` 等位置元数据。

#### Scenario: 清洗不误删表格编号
- **WHEN** 清洗含表格编号的文档
- **THEN** 表格内的编号 / 符号被保留，结构不被破坏

#### Scenario: 清洗保留位置元数据
- **WHEN** 清洗文档
- **THEN** block 的 `page` / `heading` 元数据保持不变

