## ADDED Requirements

### Requirement: 统一文档模型
系统 SHALL 将所有支持格式的文档解析为统一的 `Document` 结构：含 `doc_id` / `source` / `doc_type` 与按标题层级组织的 `sections`；每个 section 含类型化的 `blocks`（paragraph / table / list / code），并保留位置元数据（`page` / `bbox`）。

#### Scenario: PDF 解析为结构化章节与表格
- **WHEN** 解析一个含标题与表格的 PDF
- **THEN** 产出 `Document`，其 sections 反映标题层级，表格为 `type=table` 的 block 且带 `table_data`

#### Scenario: Markdown 解析为标题层级
- **WHEN** 解析一个 Markdown 文件
- **THEN** 产出 `Document`，其 sections 按 `#` / `##` 标题层级组织

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
