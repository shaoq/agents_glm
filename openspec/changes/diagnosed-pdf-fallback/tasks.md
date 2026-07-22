# Implementation Tasks

> 诊断式降级：`assess_quality` 产出 `poor_reason`（scan / layout）→ router 按原因选对应兜底（OCR / MinerU），**只降一级**。
> MinerU 重依赖可选（`try import`；未装则 layout 无兜底、跳过）。docling 内置 OCR 已处理普通扫描件，独立 OCR 仅在 docling OCR 也失败（`chars_per_page` 极低）时触发。

## 1. quality 诊断

- [ ] 1.1 `QualityReport.poor_reason()`：`scan`（`chars_per_page < SCAN_THRESHOLD` 极低，如 10）/ `layout`（`garbage_ratio > MAX` 或结构乱）/ `None`；阈值可配置（与 `is_poor` 的 50 区分——scan 阈值更严）
- [ ] 1.2 单测：scan / layout / None 三种判定

## 2. OCRParser（扫描件兜底）

- [ ] 2.1 `parsing/ocr_parser.py`：PDF 页渲染为图像 → RapidOCR 强制 OCR → 产出简化 `Document`（按页/段 paragraph block，无精细表格/标题结构）；复用 `rapidocr-onnxruntime`（docling 已带）
- [ ] 2.2 单测：OCR 产出 `Document`（mock RapidOCR 或小扫描件 fixture）

## 3. MinerUParser（复杂版面兜底，可选）

- [ ] 3.1 `parsing/mineru_parser.py`：MinerU 解析复杂版面（公式/双栏/表格）→ 统一 `Document`；`__init__` 用 `try import mineru`
- [ ] 3.2 可用性检测：`with_defaults` 检测 mineru 可导入才把 MinerUParser 配进链（未装则 layout 无兜底）

## 4. router 诊断式降级

- [ ] 4.1 `ParserRouter.parse`：主解析器 → `assess_quality` → `poor_reason` → 选对应兜底（`scan→OCR`、`layout→MinerU`）→ 只降一级 → 兜底结果再 `poor_reason` 复检；仍 poor 或无对应兜底则返回 None
- [ ] 4.2 `with_defaults`：PDF 配 `primary(Docling) + ocr_parser + mineru_parser(mineru 可选)`
- [ ] 4.3 单测：scan→OCR 降级、layout→MinerU 降级、兜底仍 poor 跳过、无兜底（mineru 未装）跳过

## 5. 端到端

- [ ] 5.1 扫描件 PDF fixture → docling poor(scan) → OCR 兜底成功
- [ ] 5.2 复杂版面 PDF fixture → docling poor(layout) → MinerU 兜底（若 mineru 已装；未装验证「跳过+日志」）
- [ ] 5.3 全测试 + 覆盖率 ≥ 80%
