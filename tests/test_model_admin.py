"""模型版本管理的接口级回归：只读挂载/当前版本/删除回退。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import viz
from app.main import app


def _registry(tmp: Path, versions: dict) -> Path:
    models = tmp / "models"
    models.mkdir(parents=True, exist_ok=True)
    reg = {"versions": versions, "updated_at": ""}
    (models / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
    return models


def test_delete_refuses_active_version(tmp_path, monkeypatch):
    models = _registry(tmp_path, {
        "v9": {"status": "active", "created_at": "2026-01-01"},
        "v10": {"status": "retired", "created_at": "2026-01-02"},
    })
    (models / "v9" / "weights").mkdir(parents=True)
    (models / "v9" / "manifest.json").write_text(
        json.dumps({"model_version": "v9", "files": {}}), encoding="utf-8")
    (models / "current").symlink_to("v9")
    (models / "v10" / "weights").mkdir(parents=True)

    monkeypatch.setattr(viz, "ROOT", tmp_path)
    client = TestClient(app)
    r = client.post("/viz/api/model/delete", json={"version": "v9"})
    assert r.status_code == 400
    assert "正在使用" in r.json()["message"]


def test_delete_moves_files_and_unregisters(tmp_path, monkeypatch):
    models = _registry(tmp_path, {
        "v9": {"status": "active", "created_at": "2026-01-01"},
        "v10": {"status": "retired", "created_at": "2026-01-02"},
    })
    (models / "v9").mkdir()
    (models / "current").symlink_to("v9")
    (models / "v10" / "weights").mkdir(parents=True)
    (models / "v10" / "weights" / "scene.pt").write_bytes(b"w")

    monkeypatch.setattr(viz, "ROOT", tmp_path)
    client = TestClient(app)
    r = client.post("/viz/api/model/delete", json={"version": "v10"})
    assert r.status_code == 200
    assert r.json()["deleted"] == "v10"
    assert not (models / "v10").exists()
    assert "v10" not in json.loads((models / "registry.json").read_text())["versions"]


def test_delete_readonly_gives_actionable_message(tmp_path, monkeypatch):
    models = _registry(tmp_path, {"v10": {"status": "retired", "created_at": "2026-01-02"}})
    (models / "v10").mkdir()
    monkeypatch.setattr(viz, "ROOT", tmp_path)
    monkeypatch.setattr(viz, "_models_writable", lambda: False)
    client = TestClient(app)
    r = client.post("/viz/api/model/delete", json={"version": "v10"})
    assert r.status_code == 409
    assert "只读" in r.json()["message"]
    # 只从列表移除仍然可用
    r2 = client.post("/viz/api/model/delete",
                     json={"version": "v10", "delete_files": False})
    assert r2.status_code == 200
    assert (models / "v10").is_dir()


def test_active_version_falls_back_to_manifest(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "current").mkdir(parents=True)
    (models / "current" / "manifest.json").write_text(
        json.dumps({"model_version": "v9"}), encoding="utf-8")
    monkeypatch.setattr(viz, "ROOT", tmp_path)
    assert viz._active_version() == "v9"
