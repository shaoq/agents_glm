import ast
from pathlib import Path


def test_reconcile_exposes_evidence_lifecycle_and_resolution_steps() -> None:
    source_path = (
        Path(__file__).parents[2] / "src" / "agents_memory" / "processing" / "reconciliation.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    subject = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "reconcile"
    )
    calls = {
        node.attr
        for node in ast.walk(subject)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }

    assert {
        "_new_messages",
        "_lifecycle_plan",
        "_select_evidence",
        "_active_targets",
        "_resolved_plan",
    } <= calls
    assert subject.end_lineno - subject.lineno < 110
