## Context

当前 PDF 降级链只有 docling 一级，`assess_quality` 只判 `is_poor`（布尔），router 顺序降级。但 poor 有两类不同原因——扫描件（OCR 失败）与复杂版面（版面模型崩）——应分别由 RapidOCR / MinerU 兜底。本变更引入「诊断式降级」：`assess_quality` 产出 poor 原因，router 按原因选对应兜底，只降一级。

关键事实：docling 默认管线**自带 RapidOCR**，普通扫描件 docling 内部已 OCR，不会进 poor。故独立 `OCRParser` 的触发条件是「docling 的 OCR 也失败」（`chars_per_page` 极低），阈值要比普通 `is_poor` 更严。

## Goals / Non-Goals

**Goals:**

- `QualityReport.poor_reason()` 诊断：`scan` / `layout` / `None`
- `OCRParser`（RapidOCR 强制图像 OCR，扫描件兜底）
- `MinerUParser`（复杂版面 / 公式 / 双栏兜底）
- `ParserRouter` 诊断式降级：按 `poor_reason` 选对应兜底，只降一级
- 兜底仍 poor 或无对应兜底 → 跳过 + 日志（不中断批量）

**Non-Goals:**

- 解析置信度 + HITL（笔记 §12.1.4，更上层的人工复核）
- 多级串行降级（Docling→MinerU→OCR 顺序）——刻意不做，只降一级
- 非 PDF 格式的降级（markdown/html/office 仍单 parser）

## Decisions

**1. 诊断式降级，非顺序降级。**
`assess_quality` 产出 `poor_reason`，router 按原因选**对应**兜底（scan→OCR、layout→MinerU），只降一级。对比顺序降级（Docling→MinerU→OCR 串行）：扫描件无需 MinerU 重模型、复杂版面不该走纯 OCR 丢结构。诊断式更精准、不浪费。

**2. `poor_reason` 判据（分层阈值）。**
```python
def poor_reason(self):
    if self.chars_per_page < SCAN_THRESHOLD:   # 极低（如 10）→ docling OCR 也失败
        return "scan"
    if self.garbage_ratio > MAX_GARBAGE:        # 乱码/版面崩
        return "layout"
    return None
```
- `scan` 阈值用**极低**（~10），区别于 `is_poor` 的 50——因为普通扫描件 docling 内置 OCR 已处理，只有 docling OCR 也失败（几乎无字）才走独立 OCR
- `layout` 用 `garbage_ratio`（可扩展：表格丢失、标题层级乱等结构信号）

**3. `OCRParser`：RapidOCR 强制图像 OCR。**
把 PDF 页面渲染成图像，跳过版面分析、纯文字识别，产出**简化 `Document`**（按页/段 paragraph block，无精细表格/标题结构）。扫描件的权衡：**有文字（丢结构）> 完全没字**。复用 `rapidocr-onnxruntime`（docling 已带，无新依赖）。

**4. `MinerUParser`：复杂版面兜底。**
MinerU（PDF-Extract-Kit）擅长公式 / 双栏 / 复杂表格，产出统一 `Document`（含 sections + 表格 block，尽力对齐 docling 输出结构）。

**5. `MinerU` 重依赖 → 可选启用。**
`mineru` 依赖很重（带多个模型）。`MinerUParser.__init__` 用 `try import`；`with_defaults` 检测 mineru 是否可导入，可导入才加入链。未安装时 `layout` poor 无兜底 → 记日志跳过（不报错）。这让 MinerU 成为「按需启用」的增强，不强加给所有部署。

**6. router 诊断式降级（只降一级）。**
```python
def parse(self, path):
    doc = primary.parse(path)
    reason = assess_quality(doc).poor_reason()
    if reason is None:
        return doc
    fallback = {"scan": self._ocr, "layout": self._mineru}.get(reason)
    if fallback is None:
        return None                       # 无对应兜底（如 mineru 未装）
    try:
        doc2 = fallback.parse(path)
        if assess_quality(doc2).poor_reason() is None:
            return doc2
    except Exception:
        pass
    return None                           # 兜底仍 poor → 跳过
```
不串行、不无限降级。兜底产出仍按 `poor_reason` 复检（防兜底也崩）。

**7. docling 内置 OCR 的定位。**
docling 默认管线含 RapidOCR，普通扫描件在 docling 内部已 OCR（不 poor）。独立 `OCRParser` 是「docling OCR 失败后的强制图像 OCR」，`scan` 阈值因此设极低（~10）。

## Risks / Trade-offs

- **[MinerU 重依赖]** → 可选启用（try import + with_defaults 检测）；未装则 layout 无兜底、跳过。
- **[`poor_reason` 阈值不准]** → scan/layout 边界靠经验；阈值可配置，配合真实 PDF 调参。
- **[OCRParser 丢结构]** → 扫描件纯文字 OCR 无表格/标题结构；权衡为「有文字 > 无」（笔记 §3.2：扫描件 OCR 是最后兜底）。
- **[MinerU/OCR 输出结构简化]** → 与 docling 的嵌套树/表格 block 不完全对齐；统一 `Document` 模型兜底（段落为主，结构尽力），下游分块/索引兼容（paragraph block 正常处理）。
- **[降级增加解析时间]** → 仅 poor 时触发兜底（二次解析）；达标文档零开销。
