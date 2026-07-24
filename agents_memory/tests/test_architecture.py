from pathlib import Path
import subprocess


def test_source_does_not_depend_on_sibling_projects() -> None:
    source_root = Path(__file__).parents[1] / "src"
    forbidden = ("agents_rag", "agents_memory_rag", "../agents_")

    for path in source_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert not any(name in content for name in forbidden), path


def test_pyproject_declares_independent_package() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "agents-memory"' in pyproject
    assert "agents_rag" not in pyproject
    assert 'agents-memory = "agents_memory.cli:app"' in pyproject


def test_runtime_storage_ignore_does_not_hide_source_package() -> None:
    ignore = (Path(__file__).parents[1] / ".gitignore").read_text(encoding="utf-8")

    assert "/storage/" in ignore.splitlines()
    assert "storage/" not in [line for line in ignore.splitlines() if not line.startswith("/")]
    repo_root = Path(__file__).parents[2]
    source = subprocess.run(
        ["git", "check-ignore", "-q", "agents_memory/src/agents_memory/storage/repository.py"],
        cwd=repo_root,
        check=False,
    )
    runtime = subprocess.run(
        ["git", "check-ignore", "-q", "agents_memory/storage/memory.sqlite"],
        cwd=repo_root,
        check=False,
    )
    assert source.returncode == 1
    assert runtime.returncode == 0
