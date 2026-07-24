import ast
from pathlib import Path


def test_commit_keeps_one_visible_transaction_and_delegates_plan_details() -> None:
    source_path = Path(__file__).parents[2] / "src" / "agents_memory" / "storage" / "coordinator.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    coordinator = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "StorageCoordinator"
    )
    methods = {node.name: node for node in coordinator.body if isinstance(node, ast.FunctionDef)}
    commit = methods["commit"]
    commit_attributes = {node.attr for node in ast.walk(commit) if isinstance(node, ast.Attribute)}

    assert {
        "_apply_defer_plan",
        "_apply_noop_plan",
        "_apply_memory_plan",
        "_build_sources",
        "_build_result",
    } <= commit_attributes | set(methods)
    plan_methods = (
        commit,
        methods["_apply_defer_plan"],
        methods["_apply_noop_plan"],
        methods["_apply_memory_plan"],
        methods["_build_sources"],
        methods["_build_result"],
    )
    assert (
        sum(
            node.attr == "transaction"
            for method in plan_methods
            for node in ast.walk(method)
            if isinstance(node, ast.Attribute)
        )
        == 1
    )
    assert commit.end_lineno - commit.lineno < 150
