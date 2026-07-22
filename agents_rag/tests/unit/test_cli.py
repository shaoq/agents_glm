"""CLI 测试（_print_report 渲染，不依赖密钥）。"""

from __future__ import annotations

from agents_rag.cli import _print_report
from agents_rag.pipeline.ingest import IngestReport


def test_print_report_renders_success(capsys):
    report = IngestReport(
        counts={"new": 2, "delete": 1, "skipped": 3, "failed": 0}, indexed_chunks=10
    )
    _print_report(report)  # 不抛错即通过


def test_print_report_renders_failures(capsys):
    report = IngestReport(
        counts={"failed": 1}, failed=[("doc1", "解析失败/低质量")], indexed_chunks=0
    )
    _print_report(report)
