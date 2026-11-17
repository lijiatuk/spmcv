"""部署换机后的路径可移植性：历史绝对路径要能重新挂到当前 ROOT。"""
from __future__ import annotations

import pytest

from app.api import viz


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "spmcv"
    target = root / "data/raw/uploads/upload_1/0488_a.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    recut = root / "data/raw/recut/01_桥放油"
    recut.mkdir(parents=True)
    (recut / "0001.png").write_bytes(b"y")
    monkeypatch.setattr(viz, "ROOT", root)
    monkeypatch.setattr(viz, "ALLOWED_ROOTS", [
        root / "data/raw/recut",
        root / "data/raw/uploads",
    ])
    return root, target


@pytest.mark.parametrize("legacy", [
    "/Users/deepway/data/spmcv/data/raw/uploads/upload_1/0488_a.jpg",
    "/app/data/raw/uploads/upload_1/0488_a.jpg",
    "data/raw/uploads/upload_1/0488_a.jpg",
])
def test_legacy_absolute_paths_rebase(fake_root, legacy):
    _root, target = fake_root
    assert viz._safe(legacy) == target.resolve()


def test_allowlist_still_enforced_after_rebase(fake_root):
    root, _target = fake_root
    # 白名单外：即便文件真实存在也不能读
    outside = root / "var/feedback.sqlite3"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"secret")
    with pytest.raises(viz.HTTPException) as exc:
        viz._safe(str(outside))
    assert exc.value.status_code == 403


def test_traversal_does_not_escape_root(fake_root):
    _root, _target = fake_root
    with pytest.raises(viz.HTTPException):
        viz._safe("/Users/deepway/data/spmcv/../../etc/passwd")


def test_missing_file_is_403_not_crash(fake_root):
    with pytest.raises(viz.HTTPException) as exc:
        viz._safe("/Users/deepway/data/spmcv/data/raw/uploads/upload_9/nope.jpg")
    assert exc.value.status_code == 403


def test_missing_file_does_not_map_to_same_basename(tmp_path, monkeypatch):
    """已删除文件的旧路径不能被误挂到 ROOT 下同名文件。"""
    root = tmp_path / "spmcv"
    other = root / "data/raw/recut/keep.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"keep")
    monkeypatch.setattr(viz, "ROOT", root)
    monkeypatch.setattr(viz, "ALLOWED_ROOTS", [root / "data/raw/recut"])
    with pytest.raises(viz.HTTPException) as exc:
        viz._safe("/old/data/other/keep.png")
    assert exc.value.status_code == 403
