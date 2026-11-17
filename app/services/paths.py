"""路径归一化：让打包/换机后的历史绝对路径仍能命中当前工作目录。

评测报告、SQLite、改标计划里存的是导出机器的绝对路径，
开发机是 `/Users/<user>/data/spmcv/data/...`，容器里是 `/app/data/...`。
文件其实都在当前仓库根目录下，只是前缀不同。
"""
from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR

ROOT = BASE_DIR


def portable_path(path_str: str | Path, root: Path | None = None) -> Path:
    """把旧机器 / 旧容器写死的绝对路径重新挂到当前 ROOT 上。

    找不到时原样返回，由调用方决定是报 404 还是跳过。
    """
    root = root or ROOT
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    try:
        if p.exists():
            return p
    except OSError:
        return p
    # 只接受从 `data/` 这一段开始的后缀：既避免把 /x/y/同名文件 误挂到 ROOT，
    # 也覆盖了 `/Users/u/proj/data/...` 里出现两次 data 的情况（取能命中的那个）。
    parts = p.parts[1:]                      # 去掉文件系统根
    for i, part in enumerate(parts):
        if part != "data":
            continue
        candidate = root.joinpath(*parts[i:])
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return p
