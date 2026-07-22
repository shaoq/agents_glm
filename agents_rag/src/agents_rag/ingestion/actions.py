"""动作两阶段执行编排：先 new / update（建新），后 delete / move。笔记 §2.6。

要点：先建后删避免空窗；动作级 try/except 失败隔离，一个失败不拖垮整批；
``SKIP`` 不执行。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from agents_rag.models import Action, ActionKind

#: 动作处理函数：接收 Action，执行实际索引操作（由编排层注入）。
ActionHandler = Callable[[Action], object]

_PHASE1 = {ActionKind.NEW, ActionKind.UPDATE}
_PHASE2 = {ActionKind.DELETE, ActionKind.MOVE}


def order_actions(actions: Iterable[Action]) -> list[Action]:
    """排序：先 new / update，再 delete / move，skip 最后（执行时跳过）。"""
    phase1 = [a for a in actions if a.kind in _PHASE1]
    phase2 = [a for a in actions if a.kind in _PHASE2]
    skips = [a for a in actions if a.kind == ActionKind.SKIP]
    return phase1 + phase2 + skips


@dataclass
class ExecutionResult:
    succeeded: list[Action] = field(default_factory=list)
    failed: list[tuple[Action, BaseException]] = field(default_factory=list)
    skipped: list[Action] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """按动作类型统计成功数 + failed / skipped 计数。"""
        c: dict[str, int] = {}
        for a in self.succeeded:
            c[a.kind.value] = c.get(a.kind.value, 0) + 1
        c["failed"] = len(self.failed)
        c["skipped"] = len(self.skipped)
        return c


def execute(
    actions: Iterable[Action],
    handler: ActionHandler,
    *,
    on_error: Callable[[Action, BaseException], None] | None = None,
) -> ExecutionResult:
    """两阶段执行：先 new / update，后 delete / move；动作级失败隔离。"""
    ordered = order_actions(actions)
    succeeded: list[Action] = []
    failed: list[tuple[Action, BaseException]] = []
    skipped: list[Action] = []
    for a in ordered:
        if a.kind == ActionKind.SKIP:
            skipped.append(a)
            continue
        try:
            handler(a)
            succeeded.append(a)
        except Exception as e:  # noqa: BLE001 — 动作级隔离，记录后继续
            failed.append((a, e))
            if on_error is not None:
                on_error(a, e)
    return ExecutionResult(succeeded=succeeded, failed=failed, skipped=skipped)
