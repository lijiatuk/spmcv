#!/usr/bin/env python3
"""量化三个「不改模型」动作的收益上限：复核策略 / 首页 VIN 规则 / 车牌规则。

输入（都是已存在的评测产物，脚本不训练、不调服务）：
  docs/reports/verified_oof.json        5 折 OOF 1,306 条预测（主口径）
  docs/reports/deployed_heldout.json    线上 fold-0 留出 241 张回放（字段/规则旁证）

输出：
  docs/reports/policy_gain_estimate_20261117.json

注意口径：
  * OOF 是「按场景抽样的图片」，不是完整工单；这里按 order 聚合只是近似，
    真正的整单指标需要完整工单黄金集。
  * 字段侧结论只基于 241 张 fold-0 留出，样本很小（首页 17 张、车牌 32 张），
    数字应读作「量级估计」，不是 SLA。

用法：
    conda run -n spmcv python scripts/estimate_policy_gain.py
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OOF = ROOT / "docs/reports/verified_oof.json"
HELDOUT = ROOT / "docs/reports/deployed_heldout.json"
OUT = ROOT / "docs/reports/policy_gain_estimate_20261117.json"

OIL = {"bridge_drain", "bridge_fill", "tire_drain", "tire_fill", "filter_none", "filter_has"}
ARRIVAL = {"arrival_plate", "license_vin", "frame_vin", "odometer"}


def group_of(label: str) -> str:
    if label in ARRIVAL:
        return "arrival"
    if label in OIL:
        return "repair"
    return "inspection"


def order_of(path: str) -> str:
    m = re.search(r"(IKD\d+)", path)
    return m.group(1) if m else path


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def suffix_distance(partial: str, full: str) -> int:
    """允许尾段比 VIN 尾部少 1~2 个字符的比对，返回最小编辑距离。"""
    best = 99
    for length in range(max(1, len(partial) - 1), min(len(full), len(partial) + 1) + 1):
        best = min(best, levenshtein(partial, full[-length:]))
    return best


# ---------------------------------------------------------------- OOF 策略扫描
def oof_image_level(preds: list[dict]) -> dict:
    """逐图口径：被复核的图 / 落在被复核工单里的错判。"""
    n, errors = len(preds), sum(1 for p in preds if not p["correct"])
    out = {}
    policies = {
        "A_global_0.85": lambda p: p["confidence"] < 0.85,
        "B_oil_0.95": lambda p: p["confidence"] < (0.95 if p["pred"] in OIL else 0.85),
        "C_oil_0.99": lambda p: p["confidence"] < (0.99 if p["pred"] in OIL else 0.85),
        "D_all_oil_review": lambda p: p["pred"] in OIL or p["confidence"] < 0.85,
    }
    for name, flag in policies.items():
        reviewed = {p["file"]: flag(p) for p in preds}
        rn = sum(reviewed.values())
        re_ = sum(1 for p in preds if reviewed[p["file"]] and not p["correct"])
        # 按工单聚合：任一图被复核，整单进复核队列
        flagged_orders = {p["order"] for p in preds if reviewed[p["file"]]}
        agg = {p["file"]: p["order"] in flagged_orders for p in preds}
        an = sum(agg.values())
        ae = sum(1 for p in preds if agg[p["file"]] and not p["correct"])
        out[name] = {
            "image_review": rn,
            "image_review_rate": round(rn / n, 4),
            "image_error_capture": round(re_ / errors, 4),
            "order_review_images": an,
            "order_review_image_rate": round(an / n, 4),
            "order_error_capture": round(ae / errors, 4),
        }
    out["_meta"] = {"n": n, "errors": errors, "orders": len({p["order"] for p in preds})}
    return out


def oof_order_level(preds: list[dict]) -> dict:
    """按业务类（arrival / repair / inspection）的工单级自动通过。

    只算场景置信度，不含字段规则；字段规则见 heldout 部分。
    """
    groups = collections.defaultdict(list)
    for p in preds:
        groups[(p["order"], group_of(p["true"]))].append(p)

    def run(arr_t: float, rep_t: float) -> dict:
        rows = {}
        for g, t in (("arrival", arr_t), ("repair", rep_t), ("inspection", arr_t)):
            items = {k: v for k, v in groups.items() if k[1] == g}
            total = len(items)
            auto = leak = 0
            for ps in items.values():
                if all(p["confidence"] >= t for p in ps):
                    auto += 1
                    if any(not p["correct"] for p in ps):
                        leak += 1
            rows[g] = {
                "groups": total,
                "auto_pass": auto,
                "auto_pass_rate": round(auto / total, 4) if total else None,
                "auto_pass_with_error": leak,
            }
        return rows

    return {
        "t_0.85_0.85": run(0.85, 0.85),
        "t_0.75_0.85": run(0.75, 0.85),
        "t_0.85_0.95": run(0.85, 0.95),
        "t_0.85_0.99": run(0.85, 0.99),
    }


# ------------------------------------------------------- heldout：字段/规则收益
def plate_ok_strict(plate: str | None) -> bool:
    if not plate:
        return False
    return len(plate) == 7 or (len(plate) == 8 and plate[-1] in "DF")


def plate_ok_loose(plate: str | None) -> bool:
    """只看长度格式，不再硬性要求新能源末位 D/F。"""
    return bool(plate) and len(plate) in (7, 8)


def heldout_field_gains(rows: list[dict]) -> dict:
    by: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by[order_of(r["file"])][r["want"]].append(r)

    # 1) 首页 VIN：尾段与同工单完整 VIN 匹配（精确 + 编辑距离<=2）
    front = [(o, r) for o, sc in by.items() for r in sc.get("service_front", [])]
    exact = fuzzy = no_full = no_part = 0
    resolved_files = []
    for o, r in front:
        fulls = [
            y["fields"].get("vin")
            for y in (by[o].get("license_vin", []) + by[o].get("frame_vin", []))
            if y.get("fields") and y["fields"].get("vin")
        ]
        part = (r.get("fields") or {}).get("vin_partial")
        if not part:
            no_part += 1
            continue
        if not fulls:
            no_full += 1
            continue
        if any(f.endswith(part) for f in fulls):
            exact += 1
            resolved_files.append(r["file"])
        elif min(suffix_distance(part, f) for f in fulls) <= 2:
            fuzzy += 1
            resolved_files.append(r["file"])
        else:
            no_full += 1

    # 2) 车牌：严格 vs 放宽末位；同工单多图是否一致
    plates = [(o, r) for o, sc in by.items() for r in sc.get("arrival_plate", [])]
    strict_ok = sum(1 for _, r in plates if plate_ok_strict((r.get("fields") or {}).get("plate")))
    loose_ok = sum(1 for _, r in plates if plate_ok_loose((r.get("fields") or {}).get("plate")))
    consistent_orders = []
    for o, sc in by.items():
        reads = [(r.get("fields") or {}).get("plate") for r in sc.get("arrival_plate", [])]
        reads = [p for p in reads if p]
        if len(reads) >= 2 and len(set(reads)) == 1 and not plate_ok_strict(reads[0]):
            consistent_orders.append({"order": o, "plate": reads[0], "n": len(reads)})

    # 3) 接车四件套（严格 vs 放宽车牌）
    def four_piece(plate_fn) -> dict:
        total = passed = 0
        for sc in by.values():
            if not all(k in sc for k in ("arrival_plate", "license_vin", "frame_vin", "odometer")):
                continue
            total += 1
            plate = any(plate_fn((r.get("fields") or {}).get("plate")) for r in sc["arrival_plate"])
            lic = any(r["fields"].get("vin_checksum_ok") for r in sc["license_vin"])
            fra = any(r["fields"].get("vin_checksum_ok") for r in sc["frame_vin"])
            odo = any(r["fields"].get("odometer") is not None for r in sc["odometer"])
            passed += int(all([plate, lic, fra, odo]))
        return {"orders": total, "pass": passed, "rate": round(passed / total, 4) if total else None}

    # 4) 检查表：首页 VIN 规则前后
    def inspection(front_fn) -> dict:
        total = passed = 0
        for o, sc in by.items():
            if "service_front" not in sc:
                continue
            total += 1
            sign_ok = bool(sc.get("service_sign")) and all(
                not any(("签字" in x or "盖章" in x) for x in (r.get("review_reasons") or []))
                for r in sc["service_sign"]
            )
            passed += int(front_fn(o, sc) and sign_ok)
        return {"orders": total, "pass": passed, "rate": round(passed / total, 4) if total else None}

    def front_before(o, sc) -> bool:
        return any((r.get("fields") or {}).get("vin") for r in sc["service_front"])

    def front_after(o, sc) -> bool:
        fulls = [
            y["fields"].get("vin")
            for y in (by[o].get("license_vin", []) + by[o].get("frame_vin", []))
            if y.get("fields") and y["fields"].get("vin")
        ]
        for r in sc["service_front"]:
            part = (r.get("fields") or {}).get("vin_partial")
            if part and fulls and min(suffix_distance(part, f) for f in fulls) <= 2:
                return True
        return False

    # 5) 图级复核数变化（241 张）
    review_before = sum(1 for r in rows if r.get("review_reasons"))
    # 首页 VIN 命中后不再复核；车牌规则放宽后末位不合规的复核消失
    front_review = sum(1 for r in rows if r["want"] == "service_front" and r.get("review_reasons"))
    plate_invalid_review = sum(
        1 for r in rows
        if r["want"] == "arrival_plate"
        and any("新能源车牌末位" in x for x in (r.get("review_reasons") or []))
    )
    return {
        "service_front": {
            "n": len(front), "exact_match": exact, "fuzzy_match": fuzzy,
            "no_full_vin": no_full, "no_partial": no_part,
            "resolvable": exact + fuzzy,
            "resolved_files": [Path(f).name for f in resolved_files],
            "order_level": {"before": inspection(front_before), "after": inspection(front_after)},
        },
        "plate": {
            "n": len(plates),
            "strict_valid": strict_ok,
            "loose_valid": loose_ok,
            "consistent_nonstandard_reads": consistent_orders,
            "loose_minus_strict": loose_ok - strict_ok,
            "four_piece_strict": four_piece(plate_ok_strict),
            "four_piece_loose": four_piece(plate_ok_loose),
        },
        "review_images": {
            "total": len(rows),
            "before": review_before,
            "before_rate": round(review_before / len(rows), 4),
            "service_front_reviews": front_review,
            "plate_invalid_end_reviews": plate_invalid_review,
            "after_rule_only": review_before - (exact + fuzzy) - plate_invalid_review,
            "after_rule_only_rate": round(
                (review_before - (exact + fuzzy) - plate_invalid_review) / len(rows), 4
            ),
        },
    }


# -------------------------------------------------------- 动作 1：审计换算表
def audit_scenarios() -> dict:
    n, errors, high_conf = 1306, 170, 60
    out = {}
    for share in (0.15, 0.20, 0.30, 0.40, 0.50):
        fixed_high = round(high_conf * share)
        delta_high = fixed_high / n
        fixed_all = round(errors * share)
        delta_all = fixed_all / n
        out[f"{int(share*100)}%"] = {
            "high_conf_fixed": fixed_high,
            "measured_top1_high_only": round(1136 / n + delta_high, 4),
            "all_errors_fixed": fixed_all,
            "measured_top1_if_rate_generalizes": round(1136 / n + delta_all, 4),
        }
    out["_meta"] = {"n": n, "errors": errors, "high_conf_errors": high_conf,
                    "current_top1": round(1136 / n, 4),
                    "max_if_all_60_were_label_errors": round(1196 / n, 4)}
    return out


def main() -> int:
    oof = json.loads(OOF.read_text(encoding="utf-8"))
    rows = json.loads(HELDOUT.read_text(encoding="utf-8"))["details"]
    report = {
        "generated_for": "docs/reports/scene_accuracy_review_20261117.md",
        "sources": {"oof": str(OOF.relative_to(ROOT)), "heldout": str(HELDOUT.relative_to(ROOT))},
        "oof_image_level": oof_image_level(oof["predictions"]),
        "oof_order_level": oof_order_level(oof["predictions"]),
        "heldout_fields": heldout_field_gains(rows),
        "audit_scenarios": audit_scenarios(),
        "caveats": [
            "OOF 是按场景抽样的图片，按 order 聚合只是近似，不是完整工单指标。",
            "heldout 只有 241 张：首页 17、车牌 32、签章 12，单张权重很大。",
            ("规则放宽（尾段模糊匹配、新能源末位不硬性 D/F）会引入误放行风险，"
             "必须先在小样本上确认，并记录 matched_by 以便审计。"),
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
