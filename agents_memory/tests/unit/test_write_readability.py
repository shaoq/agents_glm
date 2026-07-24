import ast
import inspect
from pathlib import Path

from agents_memory.pipeline.write import MemoryWritePipeline


def test_write_keeps_public_signature_and_reads_as_phase_orchestration() -> None:
    assert tuple(inspect.signature(MemoryWritePipeline.write).parameters) == (
        "self",
        "request_id",
        "scope",
        "messages",
    )

    source_path = Path(__file__).parents[2] / "src" / "agents_memory" / "pipeline" / "write.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    subject = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "write"
    )
    calls = {
        node.attr
        for node in ast.walk(subject)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }

    assert {
        "_existing_request",
        "_extract_batch",
        "_reconcile_pending",
        "_apply_reconciliation_plans",
        "_plan_candidates",
        "_commit",
    } <= calls
    assert subject.end_lineno - subject.lineno < 150
