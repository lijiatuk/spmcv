"""模型注册表一致性测试。

权重不进 git，所以在没有权重的机器上只校验结构性约束（registry/current/manifest
三者互相自洽），有权重时才校验 sha256。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
REGISTRY = MODELS / "registry.json"

ROLES = {"scene", "group", "action", "bin_axle", "bin_tire", "bin_filter", "det"}


def load_registry():
    if not REGISTRY.is_file():
        pytest.skip("没有 models/registry.json")
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def current_version():
    link = MODELS / "current"
    if not link.is_symlink():
        pytest.skip("没有 models/current 软链")
    return link.readlink().name


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_current_points_to_registered_version():
    reg = load_registry()
    ver = current_version()
    assert ver in reg["versions"], f"current 指向 {ver}，但 registry 里没有"
    assert reg["versions"][ver]["status"] == "active"


def test_manifest_roles_cover_all_heads():
    ver = current_version()
    manifest = json.loads((MODELS / ver / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["roles"]) == ROLES, "七个头必须同时存在，混搭会让融合阈值失效"


def test_manifest_files_are_hashed_and_present():
    ver = current_version()
    base = MODELS / ver
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"], "manifest.files 不能为空"
    for rel, meta in manifest["files"].items():
        path = base / rel
        if not path.is_file():
            pytest.skip(f"权重未随仓库分发（{rel}），跳过文件校验")
        assert sha256(path) == meta["sha256"], f"{rel} sha256 不匹配"


def test_every_role_has_a_weight_file():
    ver = current_version()
    base = MODELS / ver / "weights"
    missing = [r for r in ROLES if not (base / f"{r}.pt").is_file()]
    if missing:
        pytest.skip(f"权重未本地存在: {missing}")
    assert not missing
