"""Section 嵌套树构建工具。

把扁平的 ``SectionNode``（按文档顺序、带 level）列表组织成嵌套 frozen ``Section`` 树。
docling / markdown parser 各自产出扁平节点后调用 ``build_nested_sections``，
避免重复 level-stack 逻辑（笔记 §3.4 统一文档模型）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_rag.models import Block, Section


@dataclass
class SectionNode:
    """建树中间结构（可变）；遍历结束后由 ``_to_section`` 转 frozen。"""

    level: int
    heading: str | None
    blocks: list[Block] = field(default_factory=list)
    children: list[SectionNode] = field(default_factory=list)


def build_nested_sections(nodes: list[SectionNode]) -> list[Section]:
    """按 level 把扁平节点组织成嵌套 frozen ``Section`` 树。

    level-stack：节点(level=L) 挂到栈中最后一个 ``level < L`` 的节点 children；
    遇同级 / 上级则先弹出。栈空则挂顶层。
    """
    roots: list[SectionNode] = []
    stack: list[SectionNode] = []
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return [_to_section(n) for n in roots]


def _to_section(node: SectionNode) -> Section:
    """自底向上递归转 frozen ``Section``。"""
    return Section(
        heading=node.heading,
        level=node.level,
        blocks=tuple(node.blocks),
        children=tuple(_to_section(c) for c in node.children),
    )
