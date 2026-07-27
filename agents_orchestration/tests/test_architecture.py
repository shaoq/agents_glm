"""Architecture / layering boundary tests.

Enforces the dependency direction from the design::

    CLI → Application → Domain + Runtime Ports ← Infrastructure Adapters

These tests deliberately use AST import extraction (not substring matching) so
that boundary rules are not defeated by package names appearing in docstrings or
comments.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

PKG_ROOT = Path(__file__).parents[1]
SRC = PKG_ROOT / "src" / "agents_orchestration"

# Modules that represent infrastructure providers. The pure domain layer may
# import none of them; siblings/providers may only be imported inside adapters.
INFRA_PROVIDERS = {
    "sqlite3",
    "typer",
    "rich",
    "openai",
    "httpx",
    "requests",
    "aiohttp",
    "pydantic_settings",
}
SIBLINGS = {"agents_memory", "agents_rag"}
PROVIDERS = {"openai", "httpx", "requests", "aiohttp"}
PRESENTATION = {"typer", "rich"}


def _import_roots(path: Path) -> set[str]:
    """Return the set of top-level imported module names in ``path``."""

    module = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _forbid(path: Path, roots: set[str], banned: set[str], label: str) -> None:
    offenders = roots & banned
    assert not offenders, f"{path.name}: {label} -> {sorted(offenders)}"


def test_domain_imports_no_infrastructure() -> None:
    """The domain layer is pure: stdlib + pydantic only."""

    for path in (SRC / "domain").rglob("*.py"):
        _forbid(path, _import_roots(path), INFRA_PROVIDERS | SIBLINGS, "infra")


def test_domain_imports_only_pure_dependencies() -> None:
    """Domain may only depend on the standard library and pydantic."""

    allowed = {
        "agents_orchestration",
        "pydantic",
        "datetime",
        "uuid",
        "enum",
        "dataclasses",
        "typing",
        "typing_extensions",
        "collections",
        "functools",
        "itertools",
        "decimal",
        "math",
        "json",
        "time",
    }
    for path in (SRC / "domain").rglob("*.py"):
        offenders = _import_roots(path) - allowed
        assert not offenders, f"{path.name}: non-pure imports -> {sorted(offenders)}"


def test_sibling_projects_only_in_adapters() -> None:
    """``agents_memory`` / ``agents_rag`` may only be imported inside adapters."""

    for path in _python_files():
        if "adapters" in path.relative_to(SRC).parts:
            continue
        _forbid(path, _import_roots(path), SIBLINGS, "sibling outside adapters")


def test_provider_sdks_only_in_adapters() -> None:
    """Provider SDKs / HTTP clients may only be imported inside adapters."""

    for path in _python_files():
        if "adapters" in path.relative_to(SRC).parts:
            continue
        _forbid(path, _import_roots(path), PROVIDERS, "provider outside adapters")


def test_cli_is_the_only_presentation_layer() -> None:
    """``typer`` / ``rich`` are confined to the CLI entry point."""

    for path in _python_files():
        if path.name == "cli.py" and path.parent.name == "agents_orchestration":
            continue
        _forbid(path, _import_roots(path), PRESENTATION, "presentation outside cli.py")


def test_pyproject_declares_independent_package() -> None:
    pyproject = (PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "agents-orchestration"' in pyproject
    assert "agents_memory" not in pyproject
    assert "agents_rag" not in pyproject
    assert 'agents-orchestration = "agents_orchestration.cli:app"' in pyproject


def test_runtime_storage_ignore_does_not_hide_source_package() -> None:
    """Runtime storage / artifact directories are ignored, source is not."""

    ignore_lines = (PKG_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/storage/" in ignore_lines
    assert "/artifacts/" in ignore_lines

    repo_root = PKG_ROOT.parents[0]
    source = subprocess.run(
        ["git", "check-ignore", "-q", "agents_orchestration/src/agents_orchestration/cli.py"],
        cwd=repo_root,
        check=False,
    )
    runtime = subprocess.run(
        ["git", "check-ignore", "-q", "agents_orchestration/storage/runtime.sqlite"],
        cwd=repo_root,
        check=False,
    )
    artifacts = subprocess.run(
        ["git", "check-ignore", "-q", "agents_orchestration/artifacts/report.md"],
        cwd=repo_root,
        check=False,
    )
    assert source.returncode == 1
    assert runtime.returncode == 0
    assert artifacts.returncode == 0
