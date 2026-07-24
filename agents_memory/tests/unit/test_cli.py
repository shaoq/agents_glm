import json
from pathlib import Path

from typer.testing import CliRunner

from agents_memory.cli import create_app
from agents_memory.models import (
    MemoryRecord,
    MemoryType,
    WriteReport,
    WriteStatus,
)


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
