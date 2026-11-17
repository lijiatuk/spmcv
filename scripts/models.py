#!/usr/bin/env python3
"""Model registry: 冻结、校验、切换、清理模型版本。

设计目标：**权重不进 git，但版本必须可追溯、可回滚、可校验。**

    models/
      registry.json        版本索引（指标、状态、创建时间）
      current -> v5.0.0    服务默认加载的版本（软链）
      v5.0.0/
        manifest.json      文件 sha256 + 来源 checkpoint
        weights/*.pt
        onnx/*.onnx

子命令：
    list                     列出所有版本及其指标/状态
    verify [--version V]     校验 sha256（默认 current），不一致返回非零
    activate VERSION --evidence release.json
                             候选版本通过评估证据门禁后切换
    activate VERSION --rollback
                             回滚到 previous/retired 版本并校验权重
    freeze VERSION --from D  从目录冻结一个新版本（算 sha256、写 manifest）
    prune                    删除 archived 版本（保留 current 与 previous）

用法示例：
    python scripts/models.py list
    python scripts/models.py verify
    python scripts/models.py freeze v5.1.0 --from var/runs/candidate
    python scripts/models.py activate v5.2.0 --evidence docs/reports/v5.2.0_release.json
    python scripts/models.py activate v5.1.0 --rollback
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
REGISTRY = MODELS / "registry.json"

# 服务端实际加载的 7 个头
ROLES = {
    "scene": "12 类场景分类",
    "group": "3 类油液分组",
    "action": "6 类动作",
    "bin_axle": "桥 加油/放油",
    "bin_tire": "轮胎 加油/放油",
    "bin_filter": "滤芯 有/无",
    "det": "18 类检测器",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_registry() -> dict:
    if REGISTRY.is_file():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {"updated_at": "", "versions": {}}


def save_registry(data: dict) -> None:
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_current() -> str | None:
    link = MODELS / "current"
    if link.is_symlink():
        return str(link.readlink())
    return None


def valid_version_name(version: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version)) and ".." not in version


def cmd_list(_: argparse.Namespace) -> int:
    reg = load_registry()
    cur = resolve_current()
    if not reg["versions"]:
        print("registry 为空，先 freeze 一个版本")
        return 1
    print(f"{'版本':<10}{'状态':<11}{'scene top1':>11}{'创建时间':<21}{'文件数':>7}")
    for ver, meta in sorted(reg["versions"].items()):
        mark = " ← current" if ver == cur else ""
        top1 = (meta.get("metrics") or {}).get("scene", {}).get("top1")
        top1s = f"{top1:.2%}" if isinstance(top1, (int, float)) else "—"
        print(f"{ver:<10}{meta.get('status', '?'):<11}{top1s:>11}"
              f"{meta.get('created_at', '')[:19]:<21}{meta.get('n_files', 0):>7}{mark}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ver = args.version or resolve_current()
    if not ver:
        print("没有 current 版本", file=sys.stderr)
        return 2
    if not valid_version_name(ver):
        print(f"非法模型版本名: {ver}", file=sys.stderr)
        return 2
    base = MODELS / ver
    manifest = base / "manifest.json"
    if not manifest.is_file():
        print(f"缺少 {manifest}", file=sys.stderr)
        return 2

    data = json.loads(manifest.read_text(encoding="utf-8"))
    bad = []
    checked = 0
    for rel, meta in data.get("files", {}).items():
        path = base / rel
        if not path.is_file():
            bad.append((rel, "缺失"))
            continue
        checked += 1
        if sha256(path) != meta["sha256"]:
            bad.append((rel, "sha256 不匹配"))
    if bad:
        print(f"{ver}: {len(bad)} 个文件有问题")
        for rel, why in bad:
            print(f"  ✗ {rel}: {why}")
        return 1
    print(f"{ver}: {checked} 个文件全部校验通过")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    if args.activate:
        print("禁止 freeze 时直接 activate；请先生成带有门禁证据的 evidence，再执行 activate", file=sys.stderr)
        return 2
    src = Path(args.source)
    if not src.is_dir():
        print(f"源目录不存在: {src}", file=sys.stderr)
        return 2
    if not valid_version_name(args.version):
        print(f"非法模型版本名: {args.version}", file=sys.stderr)
        return 2
    dest = MODELS / args.version
    if dest.exists():
        print(f"{dest} 已存在；版本目录不可覆盖，请使用新的版本号", file=sys.stderr)
        return 2
    (dest / "weights").mkdir(parents=True)
    (dest / "onnx").mkdir(parents=True)

    files = {}
    copied = 0
    for p in sorted(src.iterdir()):
        if not p.is_file() or p.suffix not in (".pt", ".onnx"):
            continue
        sub = "onnx" if p.suffix == ".onnx" else "weights"
        target = dest / sub / p.name
        shutil.copy2(p, target)
        files[f"{sub}/{p.name}"] = {"sha256": sha256(target),
                                    "bytes": target.stat().st_size}
        copied += 1
    if not copied:
        print(f"{src} 里没有 .pt/.onnx", file=sys.stderr)
        return 2

    metrics = {}
    if args.metrics and Path(args.metrics).is_file():
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))

    manifest = {
        "model_version": args.version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roles": {k: v for k, v in ROLES.items()
                  if f"weights/{k}.pt" in files or f"onnx/{k}.onnx" in files},
        "source_dir": str(src),
        "files": files,
        "metrics": metrics,
        "notes": "sha256 由 models.py freeze 生成；服务启动时校验，不一致即拒绝启动",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    reg = load_registry()
    reg["versions"][args.version] = {
        "status": "candidate",
        "created_at": manifest["created_at"],
        "n_files": len(files),
        "metrics": metrics,
        "manifest_sha256": sha256(dest / "manifest.json"),
        "source_dir": str(src),
    }
    save_registry(reg)
    print(f"冻结 {args.version}: {len(files)} 个文件 -> {dest}")
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    ver = args.version
    if not valid_version_name(ver):
        print(f"非法模型版本名: {ver}", file=sys.stderr)
        return 2
    dest = MODELS / ver
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        print(f"{ver} 没有 manifest.json，先 freeze", file=sys.stderr)
        return 2
    reg = load_registry()
    status = reg.get("versions", {}).get(ver, {}).get("status")
    if status == "candidate":
        registered_sha = reg["versions"][ver].get("manifest_sha256")
        if not registered_sha or registered_sha != sha256(manifest_path):
            print("发布门禁未通过: registry manifest_sha256 缺失或不匹配", file=sys.stderr)
            return 2
        if not args.evidence:
            print("候选模型必须提供 --evidence 发布验收证据", file=sys.stderr)
            return 2
        error = validate_release_evidence(ver, manifest_path, Path(args.evidence))
        if error:
            print(f"发布门禁未通过: {error}", file=sys.stderr)
            return 2
    elif args.rollback:
        if status not in ("previous", "retired"):
            print(f"只能回滚到 previous/retired 版本，当前状态: {status}", file=sys.stderr)
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad = verify_model_files(dest, manifest)
        if bad:
            print(f"回滚版本权重校验失败: {bad[:3]}", file=sys.stderr)
            return 2
    else:
        print(f"版本 {ver} 状态为 {status!r}；切换候选须提供证据，回滚须使用 --rollback", file=sys.stderr)
        return 2

    prev = resolve_current()
    for meta in reg["versions"].values():
        if meta.get("status") == "active":
            meta["status"] = "previous"
    reg["versions"].setdefault(ver, {})["status"] = "active"
    (MODELS / "current").unlink(missing_ok=True)
    (MODELS / "current").symlink_to(ver)
    save_registry(reg)
    print(f"current: {prev or '(none)'} -> {ver}")
    print("提醒：服务需要重启才会加载新权重")
    return 0


def verify_model_files(base: Path, manifest: dict) -> list[tuple[str, str]]:
    bad = []
    for rel, meta in manifest.get("files", {}).items():
        path = base / rel
        if not path.is_file():
            bad.append((rel, "缺失"))
        elif sha256(path) != meta.get("sha256"):
            bad.append((rel, "sha256 不匹配"))
    return bad


def validate_release_evidence(version: str, manifest_path: Path,
                              evidence_path: Path) -> str | None:
    """Validate explicit acceptance decisions and immutable referenced reports."""
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"无法读取 manifest/evidence: {exc}"
    if evidence.get("candidate_version") != version:
        return "candidate_version 与待发布版本不一致"
    if evidence.get("manifest_sha256") != sha256(manifest_path):
        return "manifest_sha256 不匹配"
    if evidence.get("ready_to_release") is not True:
        return "ready_to_release 必须明确为 true"
    if "weights/scene.pt" not in manifest.get("files", {}):
        return "候选版本缺少必需的 weights/scene.pt"
    gates = evidence.get("acceptance")
    required = {"grouped_oof", "golden_set", "business_risk", "artifact_integrity"}
    if not isinstance(gates, dict) or not required.issubset(gates):
        return f"缺少必需验收项: {sorted(required - set(gates or {}))}"
    for name in sorted(required):
        gate = gates[name]
        if not isinstance(gate, dict) or gate.get("passed") is not True:
            return f"验收项 {name} 未通过"
        if gate.get("target") is None or gate.get("observed") is None:
            return f"验收项 {name} 必须记录 target 和 observed"
        report = gate.get("report")
        digest = gate.get("sha256")
        if not report or not digest:
            return f"验收项 {name} 必须绑定报告路径和 sha256"
        report_path = (ROOT / report).resolve()
        if ROOT not in report_path.parents or not report_path.is_file():
            return f"验收项 {name} 报告不存在或路径越界"
        if sha256(report_path) != digest:
            return f"验收项 {name} 报告 sha256 不匹配"
    bad = verify_model_files(manifest_path.parent, manifest)
    if bad:
        return f"候选模型文件校验失败: {bad[:3]}"
    return None


def cmd_prune(_: argparse.Namespace) -> int:
    reg = load_registry()
    current = resolve_current()
    removed = []
    for ver, meta in list(reg["versions"].items()):
        if ver == current or meta.get("status") in ("active", "previous"):
            continue
        path = MODELS / ver
        if path.exists():
            shutil.rmtree(path)
            removed.append(ver)
        reg["versions"].pop(ver, None)
    save_registry(reg)
    print(f"清理 {len(removed)} 个非活跃版本: {removed}" if removed else "没有可清理的版本")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="模型版本管理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    v = sub.add_parser("verify")
    v.add_argument("--version", default="")
    v.set_defaults(func=cmd_verify)

    f = sub.add_parser("freeze")
    f.add_argument("version")
    f.add_argument("--from", dest="source", required=True)
    f.add_argument("--metrics", default="")
    f.add_argument("--activate", action="store_true",
                   help="已禁用：候选必须通过 activate --evidence 发布门禁")
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_freeze)

    a = sub.add_parser("activate")
    a.add_argument("version")
    a.add_argument("--evidence", default="", help="发布验收 JSON；候选版本必填")
    a.add_argument("--rollback", action="store_true", help="仅将 previous/retired 版本回滚为 current")
    a.set_defaults(func=cmd_activate)

    sub.add_parser("prune").set_defaults(func=cmd_prune)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
