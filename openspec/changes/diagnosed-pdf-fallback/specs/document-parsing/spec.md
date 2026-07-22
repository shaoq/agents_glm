## MODIFIED Requirements

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
