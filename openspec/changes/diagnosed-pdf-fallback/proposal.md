## Why

当前 PDF 解析降级链只有 docling 一级，且 `assess_quality` 只判 `is_poor`（布尔）**不区分原因**，router 只会顺序降级。但 poor 有两类完全不同的原因——**扫描件**（文字识别失败，`chars_per_page` 极低）和**复杂版面**（版面模型崩，`garbage_ratio` 高 / 结构乱），应分别由 **RapidOCR**（纯 OCR）和 **MinerU**（强版面模型）兜底。顺序降级会浪费：扫描件不需要 MinerU 的重版面模型，复杂版面不该走纯 OCR 丢结构。

## What Changes

- `quality.py`：`QualityReport` 加 `poor_reason()` 诊断，区分 `scan`（扫描件）/ `layout`（版面崩）/ `None`（达标）
- 新增 `parsing/ocr_parser.py`：`OCRParser`（RapidOCR 强制对图像 OCR，扫描件兜底，产出统一 `Document`）
- 新增 `parsing/mineru_parser.py`：`MinerUParser`（复杂版面 / 公式 / 双栏兜底，产出统一 `Document`）
- `router.py`：从「顺序降级」升级为**诊断式降级**——按 `poor_reason` 选对应兜底（`scan → OCR`、`layout → MinerU`），**只降一级**；兜底仍 poor 或无对应兜底则跳过
- `with_defaults`：PDF 配 primary(Docling) + 诊断式选 MinerU / OCR

## Capabilities

### New Capabilities

（无——MinerU / OCR 是 document-parsing 降级链的两个兜底实现，不构成独立能力）

### Modified Capabilities

- `document-parsing`:
  - 「解析路由降级链」：从顺序降级升级为**诊断式降级**（按 poor 原因选 MinerU / RapidOCR，只降一级）
  - 「解析质量评估」：新增 `poor_reason` 诊断（区分 scan / layout）

## Impact

- **代码**：`parsing/quality.py`、`parsing/router.py`、`parsing/ocr_parser.py`(新)、`parsing/mineru_parser.py`(新)
- **依赖**：`rapidocr-onnxruntime`（docling 已带，独立 OCR 复用）；`mineru`（**重依赖**，按需启用——未安装时 layout poor 无兜底、记日志跳过）
- **非影响**：docling 主力不变；docling 内置 OCR 已处理普通扫描件，独立 `OCRParser` 仅在「docling OCR 也失败」（`chars_per_page` 极低）时触发；非 PDF 格式不受影响
