"""内容指纹：流式 SHA-256（恒定内存）+ ``(size, mtime)`` 预筛。笔记 §2.4。

身份依据是**内容指纹**而非路径：文件移动 / 改名不改身份。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

_BUF = 1 << 20  # 1MB buffer


def file_fingerprint(path: str | Path, *, buf_size: int = _BUF) -> str:
    """流式 SHA-256。分块 update 与一次性算结果相同，内存恒定。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf_size):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class FileStat:
    """``(size, mtime)`` 预筛信号。"""

    size: int
    mtime: float


def file_stat(path: str | Path) -> FileStat:
    st = os.stat(path)
    return FileStat(size=st.st_size, mtime=st.st_mtime)


def maybe_changed(path: str | Path, prev: FileStat | None) -> bool:
    """预筛：``(size, mtime)`` 一致则「可能未变」（False），否则「可能变了」（True）。

    预筛是「可能变了」，精确 hash 是「确实变了」，两者配合不会漏判变更
    （size/mtime 变了才精确 hash，没变则跳过）。
    """
    if prev is None:
        return True
    cur = file_stat(path)
    return cur.size != prev.size or cur.mtime != prev.mtime


def text_fingerprint(text: str) -> str:
    """文本指纹（embedding 缓存键的组成部分）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
