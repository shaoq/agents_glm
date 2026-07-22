## Why

当前 docling / markdown parser 产出**扁平** sections（每个标题一个平级 Section，标题层级仅记在 `level` 字段），导致多级标题（H1 > H2 > H3）的层级关系丢失——`section_path` 退化成只有单层标题，检索命中 chunk 后缺章节定位上下文。而 `structural.walk` 与 `Section.children` 字段其实**已为嵌套树设计**，能力因 parser 未填 `children` 而悬空（`test_section_path_inherited` 用手动构造的嵌套 Section 已验证 structural 对嵌套树正确工作）。

## What Changes

- `docling_parser`：解析时按 item `level` 构建 `Section.children` 嵌套树（子标题挂到父标题的 children）
- `markdown_parser`：重构 `md_to_sections`——按 `#` 层级建嵌套树，**同一标题下的段落 / 表格聚合进该 section 的 `blocks`**（当前是每 flush 一个独立 section）
- `html_parser`：自动跟随（复用 `md_to_sections`）
- `structural` / `models`：**不改**（`walk` 已递归 children、`Section.children` 已存在）
- **非破坏性**：`Document.iter_blocks` 递归遍历不受影响；office（单 section、无标题层级）不变；所有内部消费者（chunking / indexing / pipeline）均递归处理，兼容

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `document-parsing`: 「统一文档模型」requirement 细化——sections 按标题层级组织为**嵌套树**（`Section.children`）而非扁平列表；每个 section 聚合其下段落 / 表格到 `blocks`，子标题进 `children`；`section_path` 反映完整层级路径（如 `H1 > H2 > H3`）

## Impact

- **代码**：`parsing/docling_parser.py`、`parsing/markdown_parser.py`（`html_parser.py` 跟随 `md_to_sections`）
- **测试**：`tests/unit/test_parsing.py` 的 markdown 结构断言更新（扁平索引 → `children` 索引）；新增多级建树 + `section_path` 验证
- **非影响**：`structural` / `models` / `indexing` / `pipeline` / CLI 零改；office 解析不变；集成测试（对齐 / 一致断言、`iter_blocks`）不受影响
