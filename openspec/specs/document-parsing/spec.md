# document-parsing Specification

## Purpose
TBD - created by archiving change add-knowledge-base-core. Update Purpose after archive.
## Requirements
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

### Requirement: 多格式解析
系统 SHALL 按文件扩展名路由到对应解析器，支持 PDF、Markdown、HTML、Office（docx / xlsx / pptx）。

#### Scenario: 按扩展名路由
- **WHEN** 输入 `report.pdf`
- **THEN** 系统选用 docling 解析器；输入 `note.md` 则选用 markdown 解析器

### Requirement: 解析路由降级链
系统 SHALL 通过 `ParserRouter` 提供**诊断式降级**：主解析器（docling）结果质量不达标时，根据 `poor_reason`（`scan` / `layout`）选择**对应的兜底解析器**（`scan → RapidOCR` 强制图像 OCR；`layout → MinerU` 复杂版面），**只降一级**；兜底仍不达标或无对应兜底（如 MinerU 未安装）时跳过该文件并记录日志，MUST NOT 中断批量索引。

#### Scenario: 扫描件降级到 OCR
- **WHEN** docling 解析结果 `chars_per_page` 极低（docling 的内置 OCR 也失败，`poor_reason=scan`）
- **THEN** 系统选用 `OCRParser`（RapidOCR 强制图像 OCR）兜底

#### Scenario: 复杂版面降级到 MinerU
- **WHEN** docling 解析结果 `garbage_ratio` 高 / 结构乱（`poor_reason=layout`）且 MinerU 可用
- **THEN** 系统选用 `MinerUParser` 兜底

#### Scenario: 兜底仍不达标或无对应兜底则跳过
- **WHEN** 兜底解析器结果仍 `poor`，或对应兜底不可用（如 MinerU 未安装、layout 无兜底）
- **THEN** 系统跳过该文件、记录日志，且批量索引继续处理其他文件

### Requirement: 解析质量评估
系统 SHALL 对解析结果产出质量报告（至少含每页平均字符数、表格数、乱码占比等信号），并据此诊断 **poor 原因**（`poor_reason`）：`scan`（`chars_per_page` 极低，文字识别失败）/ `layout`（`garbage_ratio` 高 / 结构乱，版面崩）/ `None`（达标）。`poor_reason` 作为诊断式降级路由的判据。

#### Scenario: 扫描件诊断为 scan
- **WHEN** 解析结果 `chars_per_page` 极低（docling 内置 OCR 也失败）
- **THEN** `poor_reason` 返回 `scan`

#### Scenario: 复杂版面诊断为 layout
- **WHEN** 解析结果 `garbage_ratio` 高 / 结构乱（版面模型崩）
- **THEN** `poor_reason` 返回 `layout`

#### Scenario: 达标文档 poor_reason 为 None
- **WHEN** 解析结果质量达标
- **THEN** `poor_reason` 返回 `None`（不降级）

### Requirement: 清洗归一化
系统 SHALL 在解析后、分块前执行轻量清洗归一化（空白规整、全半角统一、去页眉页脚），按 `doc_type` 可配置；清洗 MUST NOT 破坏结构（标题层级 / 表格 / 列表），且 MUST 保留 `page` / `heading` 等位置元数据。

#### Scenario: 清洗不误删表格编号
- **WHEN** 清洗含表格编号的文档
- **THEN** 表格内的编号 / 符号被保留，结构不被破坏

#### Scenario: 清洗保留位置元数据
- **WHEN** 清洗文档
- **THEN** block 的 `page` / `heading` 元数据保持不变

