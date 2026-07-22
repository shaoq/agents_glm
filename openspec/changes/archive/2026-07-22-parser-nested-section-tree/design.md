## Context

`document-parsing` 当前产出**扁平** sections：每个标题对应一个平级 `Section`，标题层级仅记录在 `level` 字段，未用 `Section.children` 建嵌套树。后果是多级标题（H1 > H2 > H3）的层级关系丢失，`section_path` 退化成单层。

关键事实：`structural.walk` 已按「递归 children + heading stack」实现，`Section.children` 字段已存在，`Document.iter_blocks` 已递归——嵌套树的支持**早已就位**，只是 parser 没填 `children`。`test_section_path_inherited` 用手动构造的嵌套 Section 验证 structural 对嵌套树正确（52 passed）。

本变更让 docling / markdown parser 产出嵌套树，激活已有的递归能力。

## Goals / Non-Goals

**Goals:**

- `docling_parser` 按 item `level` 构建 `Section.children` 嵌套树
- `markdown_parser` 重构 `md_to_sections`：按 `#` 层级建树 + 同标题下段落/表格聚合进 `blocks`
- `section_path` 反映完整层级（如 `H1 > H2 > H3`）
- `structural` / `models` 零改

**Non-Goals:**

- office（docx/xlsx/pptx）建树——无标题层级，本就是单 section
- 层级索引 / 多粒度分块（远期增强）
- section 跨页合并（独立话题）

## Decisions

**1. 建树在 parser 侧（生产侧），不在 structural 侧（消费侧）。**
数据正确性应在生产侧保证——让 `Document` 成为文档结构的权威表示。structural 已递归（零改），符合笔记 §3.4 统一文档模型（`Section.children` 本就为嵌套设计）。备选「structural 用 level 重建 path」是消费侧补锅、逻辑重复、且 `Document` 仍是扁平，放弃。

**2. frozen 建树：可变中间结构 + 自底向上转 frozen。**
`Section` 是 frozen pydantic、`children` 是 tuple，无法边建边改。用可变中间结构（`_Node` 容器：heading/level/buffer of blocks/children list）累积，遍历结束后递归转 frozen `Section`。

**3. level-stack 算法。**
遇标题(level=L)：弹出栈顶 `level >= L` 的节点；新节点挂到栈顶的 `children`（栈空则挂顶层）；push 新节点。正文/表格 append 到当前栈顶节点的 blocks。这是标准缩进树构建，对 H1>H2>H3 自然产出嵌套。

**4. markdown blocks 聚合重构。**
当前 `md_to_sections` 每次 `flush_para`/`flush_table` 都新建独立 Section（同标题下段落和表格变成兄弟 section）。改为：当前 section 累积段落/表格到 `blocks`，遇标题才切 section。理由：§3.4 定义 `section = heading + blocks[] + children[]`，同一标题下的内容应聚合，子标题才进 `children`。

**5. docling 建树改动较小。**
docling_parser 的 `cur_blocks` 已经在聚合「当前 heading 下的 block」（遇 heading 才 flush），只需把「flush 成扁平 section」改成「按 level 挂 children」。markdown 因聚合方式不同（见决策 4），改动较大。

**6. office 不建树。**
docx 的 heading style 当前未识别（`DocxParser` 把所有段落当 paragraph），无标题层级可建；xlsx/pptx 同理。保持单 section，structural 对单 section 正常处理。

## Risks / Trade-offs

- **[markdown 聚合重构改变 section 数量]** → blocks 文本内容不变、chunk 文本不变，仅组织方式变；集成测试用「对齐 / 一致」断言（非具体数量），不受影响。
- **[扁平 → 嵌套是 sections 形状变化]** → 所有内部消费者（`iter_blocks` / `walk` / pipeline）均递归，兼容；无外部 API 契约破坏，非 BREAKING。
- **[office 不建树，与 docling/markdown 不完全一致]** → 合理（office 无标题层级）；未来若识别 docx heading style 可再建。
- **[docling 标题层级准确性依赖 layout 模型]** → 复杂版面可能识别不准；由 minerU 兜底（后置）。
