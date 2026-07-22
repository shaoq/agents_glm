"""build_nested_sections 测试：嵌套 / 同级 / 聚合 / 弹栈。"""

from __future__ import annotations

from agents_rag.models import Block
from agents_rag.parsing.tree import SectionNode, build_nested_sections


def _n(level: int, heading: str | None, blocks: list[Block] | None = None) -> SectionNode:
    return SectionNode(level=level, heading=heading, blocks=blocks or [])


def test_nested_h1_h2_h3():
    roots = build_nested_sections([_n(1, "H1"), _n(2, "H2"), _n(3, "H3")])
    assert len(roots) == 1
    assert roots[0].heading == "H1"
    assert roots[0].children[0].heading == "H2"
    assert roots[0].children[0].children[0].heading == "H3"


def test_siblings_same_level():
    roots = build_nested_sections([_n(1, "A"), _n(2, "B"), _n(2, "C")])
    assert roots[0].heading == "A"
    assert [c.heading for c in roots[0].children] == ["B", "C"]


def test_blocks_aggregated_into_section():
    roots = build_nested_sections([_n(1, "H1", [Block(text="a"), Block(text="b")])])
    assert [bl.text for bl in roots[0].blocks] == ["a", "b"]


def test_single_node_no_heading():
    roots = build_nested_sections([_n(1, None, [Block(text="x")])])
    assert len(roots) == 1
    assert roots[0].heading is None


def test_level_pop_after_deep_then_back():
    # H1 > H2a > H3，然后 H2b 应挂回 H1（不是 H3）
    roots = build_nested_sections([_n(1, "H1"), _n(2, "H2a"), _n(3, "H3"), _n(2, "H2b")])
    h1 = roots[0]
    assert [c.heading for c in h1.children] == ["H2a", "H2b"]
    assert h1.children[0].children[0].heading == "H3"
    assert len(h1.children[1].children) == 0
