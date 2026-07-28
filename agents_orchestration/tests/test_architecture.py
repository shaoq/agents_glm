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

import pytest

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


def _imported_modules(path: Path) -> set[str]:
    """Return the set of fully-qualified imported module names in ``path``.

    Unlike :func:`_import_roots`, this preserves the full dotted path so layer
    rules such as "runtime must not import agents_orchestration.application"
    can be checked precisely. Relative imports (``from .`` / ``from ..``) are
    rejected so layer rules cannot be bypassed by a non-resolved relative path.
    """

    module = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise NotImplementedError(
                    f"Relative import (level={node.level}) in {path}; use absolute imports"
                )
            if node.module:
                mods.add(node.module)
    return mods


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
        "__future__",
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
        "hashlib",
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


def test_adapters_do_not_read_sibling_storage_or_env_files() -> None:
    """Adapters never open sibling databases or ``.env`` directly (task 7.10).

    Secrets arrive via Settings at the composition root; sibling public APIs are
    the only permitted touch points. ``sqlite3`` / ``dotenv`` are forbidden here.
    """

    forbidden = {"sqlite3", "dotenv"}
    for path in (SRC / "adapters").rglob("*.py"):
        offenders = _import_roots(path) & forbidden
        assert not offenders, f"{path.name}: adapter reads forbidden infra: {sorted(offenders)}"


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


# ---------------------------------------------------------------------------
# Task 1.3 — Application → RunCoordinator → Task Runtime dependency direction
# ---------------------------------------------------------------------------


def test_runtime_does_not_import_application_layer() -> None:
    """The Task Runtime / persistence must not depend back up to the
    Application layer (task 1.3). The future RunCoordinator (Ch.4) sits between
    Application and Task Runtime and MUST inherit this same downward direction:
    Application → RunCoordinator → Task Runtime — never the reverse."""

    for path in (SRC / "runtime").rglob("*.py"):
        offenders = {
            m
            for m in _imported_modules(path)
            if m == "agents_orchestration.application"
            or m.startswith("agents_orchestration.application.")
        }
        assert not offenders, (
            f"{path.name}: runtime imports application layer -> {sorted(offenders)}"
        )


# ---------------------------------------------------------------------------
# Task 1.4 — CLI and phase handlers must not import SQLite implementations
# ---------------------------------------------------------------------------


def test_cli_and_phase_handlers_isolate_sqlite() -> None:
    """``cli.py`` and the orchestration phase handlers must depend on ports and
    repositories, never on ``sqlite3`` directly (task 1.4). SQLite is confined
    to ``runtime/persistence``; every other layer goes through the UnitOfWork."""

    forbidden = {"sqlite3"}
    targets: list[Path] = [SRC / "cli.py"]
    targets += sorted((SRC / "orchestration").rglob("*.py"))
    for path in targets:
        offenders = _import_roots(path) & forbidden
        assert not offenders, f"{path.name}: direct sqlite3 import -> {sorted(offenders)}"


# ---------------------------------------------------------------------------
# Task 1.5 — sibling projects must not depend on agents_orchestration
# ---------------------------------------------------------------------------


def test_sibling_projects_do_not_depend_on_orchestration() -> None:
    """``agents_memory`` / ``agents_rag`` must never import
    ``agents_orchestration`` (task 1.5). The orchestrator depends on their
    public APIs; the reverse would invert the dependency direction."""

    repo_root = PKG_ROOT.parent
    for sibling in ("agents_memory", "agents_rag"):
        sibling_src = repo_root / sibling / "src"
        if not sibling_src.exists():
            pytest.skip(f"{sibling}/src not present in this worktree")
        for path in sorted(sibling_src.rglob("*.py")):
            offenders = {
                m
                for m in _imported_modules(path)
                if m == "agents_orchestration" or m.startswith("agents_orchestration.")
            }
            assert not offenders, (
                f"{sibling}/{path.relative_to(sibling_src)}: sibling imports "
                f"agents_orchestration -> {sorted(offenders)}"
            )
