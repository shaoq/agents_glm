import json
from pathlib import Path

from typer.testing import CliRunner

from agents_memory.cli import create_app
from agents_memory.models import (
    MemoryRecord,
    MemoryType,
    PendingResolutionStatus,
    WriteReport,
    WriteStatus,
)
from agents_memory.recall import (
    ExecutionStatus,
    RecallMetadata,
    RecallResult,
    Sufficiency,
    TemporalIntent,
)
from agents_memory.recall.errors import RecallStorageUnavailable
from agents_memory.service import MemoryService
from agents_memory.storage.repository import MemoryRepository


class StubPipeline:
    def write(self, **kwargs):
        return WriteReport(
            request_id=kwargs["request_id"],
            status=WriteStatus.SUCCESS,
            sqlite_committed=True,
            index_synced=True,
        )


class StubService:
    def list_memories(self, scope, type=None, include_history=False):
        return [
            MemoryRecord(
                id="m1",
                scope=scope,
                type=MemoryType.FACT,
                content="用户偏好 Python",
                importance=8,
                confidence=0.9,
            )
        ]

    def get_memory(self, memory_id, user_id):
        return None

    def delete_memory(self, memory_id, user_id):
        return True

    def repair_index(self):
        return 2

    def rebuild_index(self):
        return 3

    def list_pending_resolutions(self, scope, status=PendingResolutionStatus.OPEN):
        return []

    def sweep_pending_resolutions(self):
        return 2


def test_cli_write_outputs_json(tmp_path: Path) -> None:
    payload = {
        "request_id": "req-1",
        "scope": {"user_id": "u1"},
        "messages": [{"message_id": "m1", "role": "user", "content": "hello"}],
    }
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        create_app(StubPipeline(), StubService()),
        ["write", str(path), "--json-output"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["request_id"] == "req-1"


def test_cli_write_accepts_stdin() -> None:
    payload = {
        "request_id": "req-stdin",
        "scope": {"user_id": "u1"},
        "messages": [{"message_id": "m1", "role": "user", "content": "hello"}],
    }

    result = CliRunner().invoke(
        create_app(StubPipeline(), StubService()),
        ["write", "--json-output"],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["request_id"] == "req-stdin"


def test_cli_list_delete_and_sync_commands() -> None:
    runner = CliRunner()
    app = create_app(StubPipeline(), StubService())

    listed = runner.invoke(app, ["list", "--user-id", "u1", "--json-output"])
    deleted = runner.invoke(app, ["delete", "m1", "--user-id", "u1"])
    repaired = runner.invoke(app, ["sync", "repair"])
    rebuilt = runner.invoke(app, ["sync", "rebuild"])

    assert listed.exit_code == 0 and "用户偏好 Python" in listed.stdout
    assert deleted.exit_code == 0 and "deleted" in deleted.stdout
    assert repaired.exit_code == 0 and "2" in repaired.stdout
    assert rebuilt.exit_code == 0 and "3" in rebuilt.stdout


def test_cli_list_uses_sqlite_only_runtime(monkeypatch) -> None:
    def fail_runtime():
        raise AssertionError("full runtime must not be built")

    monkeypatch.setattr("agents_memory.cli.build_runtime", fail_runtime)
    monkeypatch.setattr(
        "agents_memory.cli.build_read_service", lambda: StubService()
    )

    result = CliRunner().invoke(
        create_app(), ["list", "--user-id", "u1", "--json-output"]
    )

    assert result.exit_code == 0


def test_cli_pending_list_and_sweep() -> None:
    runner = CliRunner()
    app = create_app(StubPipeline(), StubService())

    listed = runner.invoke(
        app,
        [
            "pending",
            "list",
            "--user-id",
            "u1",
            "--status",
            "open",
            "--json-output",
        ],
    )
    swept = runner.invoke(app, ["pending", "sweep"])

    assert listed.exit_code == 0
    assert json.loads(listed.stdout) == []
    assert swept.exit_code == 0
    assert "2" in swept.stdout


# === recall command ===


class _RecordingRecallPipeline:
    """Records the RecallRequest and returns a canned RecallResult (or raises)."""

    def __init__(
        self,
        result: RecallResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._result = result
        self._raises = raises
        self.calls: list = []

    def run(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _canned_recall_result(
    context: str = "用户偏好 Python",
    diagnostics: tuple[str, ...] = (),
) -> RecallResult:
    return RecallResult(
        context=context,
        evidence=(),
        metadata=RecallMetadata(
            sufficiency=Sufficiency.SUFFICIENT,
            execution_status=ExecutionStatus.COMPLETE,
            diagnostics=diagnostics,
        ),
    )


def _recall_app(tmp_path: Path, pipeline: _RecordingRecallPipeline):
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    service = MemoryService(repository, recall_pipeline=pipeline)
    return create_app(service=service)


def test_cli_recall_human_readable(tmp_path: Path) -> None:
    app = _recall_app(
        tmp_path, _RecordingRecallPipeline(result=_canned_recall_result())
    )
    result = CliRunner().invoke(app, ["recall", "--user-id", "u1", "用户偏好"])

    assert result.exit_code == 0
    assert "用户偏好 Python" in result.stdout


def test_cli_recall_json_output(tmp_path: Path) -> None:
    app = _recall_app(
        tmp_path, _RecordingRecallPipeline(result=_canned_recall_result())
    )
    result = CliRunner().invoke(
        app, ["recall", "--user-id", "u1", "q", "--json-output"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["context"] == "用户偏好 Python"
    assert payload["metadata"]["sufficiency"] == "sufficient"


def test_cli_recall_diagnostic_output(tmp_path: Path) -> None:
    pipeline = _RecordingRecallPipeline(
        result=_canned_recall_result(diagnostics=("intent_fallback",))
    )
    app = _recall_app(tmp_path, pipeline)
    result = CliRunner().invoke(
        app, ["recall", "--user-id", "u1", "q", "--diagnostic"]
    )

    assert result.exit_code == 0
    assert pipeline.calls[0].diagnostic is True
    assert "intent_fallback" in result.stdout


def test_cli_recall_maps_scope_type_and_budget(tmp_path: Path) -> None:
    pipeline = _RecordingRecallPipeline(result=_canned_recall_result())
    app = _recall_app(tmp_path, pipeline)
    CliRunner().invoke(
        app,
        [
            "recall",
            "--user-id",
            "u1",
            "--agent-id",
            "a1",
            "--session-id",
            "s1",
            "--type",
            "fact",
            "--max-evidence",
            "5",
            "--max-tokens",
            "500",
            "查询",
        ],
    )

    request = pipeline.calls[0]
    assert request.user_id == "u1"
    assert request.agent_id == "a1"
    assert request.session_id == "s1"
    assert MemoryType.FACT in request.explicit_types
    assert request.max_evidence_items == 5
    assert request.max_context_tokens == 500


def test_cli_recall_maps_temporal_arguments(tmp_path: Path) -> None:
    pipeline = _RecordingRecallPipeline(result=_canned_recall_result())
    app = _recall_app(tmp_path, pipeline)
    CliRunner().invoke(
        app,
        [
            "recall",
            "--user-id",
            "u1",
            "--temporal",
            "interval",
            "--time-start",
            "2026-01-01T00:00:00",
            "--time-end",
            "2026-06-01T00:00:00",
            "查询",
        ],
    )

    request = pipeline.calls[0]
    assert request.temporal_intent == TemporalIntent.INTERVAL
    assert request.explicit_time_range is not None
    assert request.explicit_time_range.start.year == 2026
    assert request.explicit_time_range.end.year == 2026


def test_cli_recall_reports_not_configured(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    app = create_app(service=MemoryService(repository))
    result = CliRunner().invoke(app, ["recall", "--user-id", "u1", "q"])

    assert result.exit_code != 0
    assert "not configured" in result.output


def test_cli_recall_reports_domain_failure(tmp_path: Path) -> None:
    pipeline = _RecordingRecallPipeline(
        raises=RecallStorageUnavailable("sqlite down")
    )
    app = _recall_app(tmp_path, pipeline)
    result = CliRunner().invoke(app, ["recall", "--user-id", "u1", "q"])

    assert result.exit_code == 1


def test_cli_help_lists_recall_command() -> None:
    result = CliRunner().invoke(create_app(StubPipeline(), StubService()), ["--help"])

    assert result.exit_code == 0
    assert "recall" in result.stdout
