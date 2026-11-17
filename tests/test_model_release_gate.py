from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scripts import models


def _candidate(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    model_root = root / "models"
    model_root.mkdir(parents=True)
    version = model_root / "v-test"
    (version / "weights").mkdir(parents=True)
    (version / "weights/scene.pt").write_bytes(b"candidate-model")
    manifest = {
        "model_version": "v-test",
        "files": {
            "weights/scene.pt": {
                "sha256": models.sha256(version / "weights/scene.pt"),
                "bytes": (version / "weights/scene.pt").stat().st_size,
            }
        },
    }
    manifest_path = version / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry_path = model_root / "registry.json"
    registry_path.write_text(json.dumps({"versions": {"v-test": {
        "status": "candidate", "manifest_sha256": models.sha256(manifest_path),
    }}}))
    monkeypatch.setattr(models, "ROOT", root)
    monkeypatch.setattr(models, "MODELS", model_root)
    monkeypatch.setattr(models, "REGISTRY", registry_path)
    return root, model_root, manifest_path, registry_path


def _write_evidence(root: Path, manifest_path: Path) -> Path:
    report = root / "reports/accepted.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"result":"verified"}', encoding="utf-8")
    gate = {
        "passed": True,
        "target": "approved target",
        "observed": "measured value",
        "report": "reports/accepted.json",
        "sha256": models.sha256(report),
    }
    evidence = {
        "candidate_version": "v-test",
        "manifest_sha256": models.sha256(manifest_path),
        "ready_to_release": True,
        "acceptance": {
            name: gate for name in (
                "grouped_oof", "golden_set", "business_risk", "artifact_integrity"
            )
        },
    }
    path = root / "release-evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def test_candidate_activation_requires_release_evidence(tmp_path, monkeypatch):
    _, model_root, _, _ = _candidate(tmp_path, monkeypatch)
    result = models.cmd_activate(Namespace(version="v-test", evidence="", rollback=False))
    assert result == 2
    assert not (model_root / "current").exists()


def test_candidate_activation_accepts_complete_matching_evidence(tmp_path, monkeypatch):
    root, model_root, manifest, registry = _candidate(tmp_path, monkeypatch)
    evidence = _write_evidence(root, manifest)
    result = models.cmd_activate(Namespace(
        version="v-test", evidence=str(evidence), rollback=False
    ))
    assert result == 0
    assert (model_root / "current").readlink() == Path("v-test")
    assert json.loads(registry.read_text())["versions"]["v-test"]["status"] == "active"


def test_evidence_rejects_tampered_report(tmp_path, monkeypatch):
    root, _, manifest, _ = _candidate(tmp_path, monkeypatch)
    evidence = _write_evidence(root, manifest)
    (root / "reports/accepted.json").write_text("tampered", encoding="utf-8")
    assert "sha256 不匹配" in models.validate_release_evidence("v-test", manifest, evidence)


def test_freeze_never_overwrites_an_existing_version(tmp_path, monkeypatch):
    root, model_root, _, registry = _candidate(tmp_path, monkeypatch)
    source = root / "candidate-source"
    source.mkdir()
    (source / "scene.pt").write_bytes(b"replacement")
    result = models.cmd_freeze(Namespace(
        source=str(source), version="v-test", activate=False, force=True, metrics=""
    ))
    assert result == 2
    assert (model_root / "v-test/weights/scene.pt").read_bytes() == b"candidate-model"
    assert json.loads(registry.read_text())["versions"]["v-test"]["status"] == "candidate"


def test_prune_preserves_current_even_if_registry_status_is_stale(tmp_path, monkeypatch):
    _, model_root, _, registry = _candidate(tmp_path, monkeypatch)
    (model_root / "current").symlink_to("v-test")
    registry.write_text(json.dumps({"versions": {"v-test": {"status": "candidate"}}}))
    assert models.cmd_prune(Namespace()) == 0
    assert (model_root / "v-test/manifest.json").is_file()
    assert (model_root / "current").readlink() == Path("v-test")


def test_model_version_name_cannot_escape_model_registry():
    assert models.valid_version_name("v5.2.0-rc1")
    assert not models.valid_version_name("../outside")
    assert not models.valid_version_name("v5/2")
