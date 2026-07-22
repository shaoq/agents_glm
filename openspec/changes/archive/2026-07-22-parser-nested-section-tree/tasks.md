# Implementation Tasks

> 实现依据：[proposal](./proposal.md) · [design](./design.md) · [specs](./specs/)。
> 核心思路：抽一个公共 `build_nested_sections`（level-stack + frozen 组装），docling / markdown parser 各自产出扁平节点列表后调用它建嵌套树，避免重复逻辑。

## 1. 公共建树工具

- [x] 1.1 新建 `parsing/tree.py`：`build_nested_sections(nodes: list[SectionNode]) -> list[Section]`，其中 `SectionNode = (level, heading, blocks)`；内部用 level-stack（弹 `level >= 当前` 的节点挂 children）+ 自底向上递归转 frozen `Section`
- [x] 1.2 单测 `test_tree.py`：H1>H2>H3 正确嵌套、同级并列、无标题单节点、blocks 聚合

## 2. markdown_parser 接入

- [x] 2.1 重构 `md_to_sections`：改为产出扁平 `SectionNode` 列表（同一 `#` 标题下的段落 / 表格聚合进该节点的 `blocks`，遇新标题开新节点），再调 `build_nested_sections` 返回嵌套树
- [x] 2.2 单测：`#`/`##`/`###` 嵌套、表格归属正确、无标题整篇单 section

## 3. docling_parser 接入

- [x] 3.1 重构 `docling_parser.parse`：用 `_Node` 累积当前 heading 的 blocks（段落 / 表格），遇标题产出 `SectionNode(level=item.level, ...)`，遍历结束调 `build_nested_sections`
- [x] 3.2 验证：`tests/fixtures/sample.pdf` 解析出嵌套 sections（端到端，docling）

## 4. 测试更新与验证

- [x] 4.1 更新 `test_parsing.py`：`test_markdown_headings_levels`（改 `sections[0].children[0]`）、`test_markdown_table_block`（递归找 table block）
- [x] 4.2 新增测试：多级标题 → `split_parents` 产出的父块 `section_path` 含 `H1 > H2 > H3`
- [x] 4.3 跑全部测试 + 覆盖率 ≥ 80%
- [x] 4.4 端到端 `agents-rag ingest tests/fixtures`（真实密钥），确认 section_path 多级、索引正常
