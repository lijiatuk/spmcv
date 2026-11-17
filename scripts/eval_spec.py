#!/usr/bin/env python3
"""Run the 11 spec scenarios (images embedded in SPM系统AI识别.xlsx) end to end.

The images live in data/external/spec/<class folder>/spec_imageN.png. Folder
names match app/scenes.json, so the expected scene comes from the folder.

Usage:
    python scripts/eval_spec.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / "data/external/spec"
API = "http://127.0.0.1:8017/v1/analyze"
CONFIG = json.loads((ROOT / "app/scenes.json").read_text(encoding="utf-8"))
CLASS_MAP = CONFIG["class_map"]
ACTION_SLUG = {
    "bridge_drain": "drain", "bridge_fill": "fill",
    "tire_drain": "drain", "tire_fill": "fill",
    "filter_none": "none", "filter_has": "has",
}


async def one(client: httpx.AsyncClient, path: Path, meta: dict):
    data = path.read_bytes()
    try:
        resp = await client.post(API, files={"image": (path.name, data, "image/png")},
                                 timeout=180)
        resp.raise_for_status()
        return path, meta, resp.json()
    except Exception as exc:  # noqa: BLE001
        return path, meta, {"error": f"{type(exc).__name__}: {exc}"}


async def main() -> int:
    jobs = []
    for folder in sorted(EXTRA.iterdir()):
        meta = CLASS_MAP.get(folder.name)
        if not meta:
            continue
        for path in sorted(folder.glob("*")):
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                jobs.append((path, meta))
    if not jobs:
        print("no spec images under", EXTRA)
        return 1

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[one(client, p, m) for p, m in jobs])

    rows, scene_ok, action_ok = [], 0, 0
    for path, meta, data in results:
        got_scene = data.get("scene")
        want_scene = meta["scene"]
        scene_match = got_scene == want_scene
        scene_ok += scene_match
        fields = data.get("fields") or {}
        action = (data.get("conclusion") or {}).get("action")
        want_action = ACTION_SLUG.get(meta["slug"])
        if want_action is not None:
            action_ok += action == want_action
        rows.append({
            "image": path.name,
            "want_scene": want_scene,
            "got_scene": got_scene,
            "scene_ok": scene_match,
            "want_action": want_action,
            "action": action,
            "vin": fields.get("vin"),
            "vin_partial": fields.get("vin_partial"),
            "plate": fields.get("plate"),
            "odometer": fields.get("odometer"),
            "review": data.get("need_review"),
            "latency_ms": data.get("latency_ms"),
            "statement": data.get("statement"),
        })

    print(f"{'image':22s} {'scene':16s} {'ok':3s} {'action':8s} {'want':5s} "
          f"{'plate':12s} {'vin':20s} {'odo':6s} review")
    for r in rows:
        print(f"{r['image']:22s} {str(r['got_scene']):16s} "
              f"{'Y' if r['scene_ok'] else 'N':3s} {str(r['action']):8s} "
              f"{str(r['want_action'] or '-'):5s} {str(r['plate'] or '-'):12s} "
              f"{str(r['vin'] or '-'):20s} {str(r['odometer'] or '-'):6s} {r['review']}")

    print(f"\nscene {scene_ok}/{len(rows)} = {scene_ok / len(rows):.1%}")
    action_rows = [r for r in rows if r["want_action"]]
    print(f"action {action_ok}/{len(action_rows)} = {action_ok / len(action_rows):.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
