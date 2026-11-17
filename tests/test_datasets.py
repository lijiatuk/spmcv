"""训练集切分的单元测试：按工单分组、去重、manifest 自洽。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import datasets


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_build_upload_dataset_keeps_orders_in_one_split(tmp_path, monkeypatch):
    root = tmp_path
    batch = root / "data/raw/uploads/b1"
    # 6 个工单，每个 2 张；标签覆盖两个类
    for i in range(6):
        order = f"IKD2026{i:04d}"
        _write(batch / "arrival_plate" / f"{order}_a.jpg", f"a{i}".encode())
        _write(batch / "bridge_drain" / f"{order}_b.jpg", f"b{i}".encode())
    # 同一个工单的重复上传（内容相同、文件名不同）应被去重
    _write(batch / "arrival_plate" / "IKD20260000_dup.jpg", b"a0")

    monkeypatch.setattr(datasets, "ROOT", root)
    manifest = datasets.build_dataset(
        source="upload:b1", name="unit", out_root=root / "out")

    assert manifest["ratios"] == {"train": 0.7, "val": 0.15, "test": 0.15}
    assert manifest["total"] == 12, "重复图片必须去重"
    assert sum(manifest["counts"].values()) == manifest["total"]
    assert manifest["counts"]["train"] > 0
    assert manifest["counts"]["val"] > 0
    assert manifest["counts"]["test"] > 0

    by_order: dict[str, set[str]] = {}
    for f in manifest["files"]:
        by_order.setdefault(f["order"], set()).add(f["split"])
    assert all(len(splits) == 1 for splits in by_order.values()), "同一工单不能跨 split"

    saved = json.loads((Path(manifest["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert saved["content_hash"]
    assert len(saved["files"]) == manifest["total"]


def test_build_rejects_bad_ratios(tmp_path, monkeypatch):
    batch = tmp_path / "data/raw/uploads/b2"
    _write(batch / "arrival_plate" / "IKD1_a.jpg", b"x")
    monkeypatch.setattr(datasets, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        datasets.build_dataset(source="upload:b2", name="bad",
                               ratios={"train": 0.5, "val": 0.2, "test": 0.2},
                               out_root=tmp_path / "out")
