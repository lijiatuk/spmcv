# SPM 视觉识别 · 业务接口说明（联调版）

> **已过时（2026-11-17）**：对外契约以 [`docs/上层调用接口文档.md`](../上层调用接口文档.md) 为准。
> 本文里的 `ok` / `auto_approved` 已在 2.1 版本移除，统一为 `result`（`pass` / `partial` / `review`）。

> 适用版本：v2.0 契约 / 模型 v5.1.0  
> 在线调试：部署后打开 `http://<服务器>:8020/docs`（Swagger，可直接上传图片试）  
> 机器可读契约：`http://<服务器>:8020/openapi.json`

本文面向业务系统开发，只写**调哪个接口、传什么、返回什么、怎么判断通过**。
内部实现和模型细节不在本文范围。

---

## 1. 一分钟联调

准备一张接车照片 `arrival.jpg`：

```bash
BASE=http://10.51.13.7:8020

# 1) 看服务是否正常、模型版本、限制
curl -s $BASE/v1/meta | jq '{version, model: .model.configured_version, limits}'

# 2) 单张识别（自动判断是什么照片）
curl -s -X POST $BASE/v1/analyze \
  -F "image=@arrival.jpg" | jq

# 3) 按业务分类识别（推荐）
curl -s -X POST $BASE/v1/arrival \
  -F "images=@arrival.jpg" \
  -F "expected_plate=晋D08906D" | jq

# 4) 完整工单（多张图一起校验）
curl -s -X POST $BASE/v1/workorder \
  -F "work_order_id=WO-2026-0001" \
  -F "expected_plate=晋D08906D" \
  -F "expected_odometer=182" \
  -F "images=@arrival.jpg" \
  -F "images=@license.jpg" \
  -F "images=@frame.jpg" \
  -F "images=@odometer.jpg" | jq '{auto_approved,need_review,review_reasons,missing_scenes}'
```

判断规则只有一句：**HTTP 200 不代表业务通过**。单张看 `need_review`，
整单看 `auto_approved`。原因同时给中文 `review_reasons` 和稳定码 `codes`。

---

## 2. 接口一览

| 方法 | 路径 | 用途 | 图片上限 |
|---|---|---|---|
| GET | `/v1/meta` | 契约、模型版本、并发/大小限制 | — |
| GET | `/healthz` | 进程存活 | — |
| GET | `/readyz` | 依赖（vision/ocr）就绪 | — |
| POST | `/v1/analyze` | 单张识别，返回场景 + OCR 字段 + 结论 | 1 张 |
| POST | `/v1/scan` | 统一入口，可传 `category` 做分类校验 | 20 张 |
| POST | `/v1/arrival` | 接车照片：车牌 / 行驶证 VIN / 车架 VIN / 里程 | 20 张 |
| POST | `/v1/repair` | 故障及修复：桥、轮胎加/放油，有/无滤芯 | 20 张 |
| POST | `/v1/inspection` | 项目检查表：服务单首页 VIN、末页签字盖章 | 20 张 |
| POST | `/v1/workorder` | 完整工单跨图校验（推荐业务系统用这个） | 20 张 |

所有图片接口都是 `multipart/form-data`；多图字段名统一为 `images`（可重复）。
单图接口字段名是 `image`。

---

## 3. 请求参数

### 3.1 通用

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `images` | file[] | 是 | jpg/png/webp，单张 ≤ 15MB，整单 ≤ 80MB |
| `work_order_id` | string | 否 | 工单号，原样回传，用于日志追踪 |

### 3.2 业务比对字段（可选，传了才比对）

| 字段 | 适用接口 | 说明 |
|---|---|---|
| `expected_plate` | arrival / scan / workorder | 工单车牌；与图片识别结果不一致 → 复核 |
| `expected_odometer` | arrival / scan / workorder | 工单里程；与图片识别结果偏差 > 5% → 复核 |
| `expected_vin` | workorder | 工单 VIN；与图片 VIN 不一致 → 复核 |
| `axle_type` | repair / workorder | `hande` 或其它；汉德桥不做轮胎/滤芯判定 |
| `expected_action` | repair / scan / workorder | 期望动作：`drain` / `fill` / `has` / `none` |
| `category` | scan | `arrival` / `repair` / `inspection`；不传只识别场景 |
| `strict` | arrival / scan | `1` = 四件套不齐也算不通过；默认 0 只校验本次提交的图 |

> **单张与整单的区别**：`/v1/arrival` 默认只判断你传了的照片。只传一张车头照，
> 不会因为“没传行驶证/里程表”报错，返回 `status=partial`；
> 要整单四件套齐备才能通过，传 `strict=1` 或直接用 `/v1/workorder`。

---

## 4. 返回结构

### 4.1 单张 `/v1/analyze`

```json
{
  "scene": "arrival_plate",
  "scene_confidence": 0.9999,
  "conclusion": null,
  "fields": {"plate": "晋D08906D", "vin": null, "odometer": null},
  "need_review": false,
  "review_reasons": [],
  "statement": "匹配接车照片，识别到车牌号 晋D08906D",
  "model_version": "v5.1.0",
  "latency_ms": 420,
  "work_order_id": ""
}
```

### 4.2 分类接口 `/v1/arrival`（repair / inspection 同构）

```json
{
  "category": "arrival",
  "ok": true,
  "complete": false,
  "status": "partial",
  "plate": "晋D08906D",
  "vin": {"license": null, "frame": null, "match": false},
  "odometer": null,
  "present": ["arrival_plate"],
  "missing_scenes": ["license_vin", "frame_vin", "odometer"],
  "missing_reasons": ["未提交行驶证照片", "未提交车架铭牌照片", "未提交里程表照片"],
  "reasons": [],
  "hints": [],
  "photos": [
    {"index": 0, "filename": "arrival.jpg", "scene": "arrival_plate",
     "confidence": 0.9999, "need_review": false, "review_reasons": []}
  ],
  "unexpected": [],
  "need_review": false
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `ok` | 本次提交的照片是否全部通过 |
| `complete` | 该业务分类的照片是否齐全（arrival 为四件套，repair/inspection 为对应场景） |
| `status` | `pass` 全部通过；`partial` 本张通过但整单未齐；`review` 需人工 |
| `reasons` | 需要人工处理的原因（中文） |
| `missing_scenes` | 没提交的照片类型；不是识别失败 |
| `photos[].need_review` | 单张照片本身的结论 |

### 4.3 整单 `/v1/workorder`

```json
{
  "work_order_id": "WO-2026-0001",
  "auto_approved": false,
  "need_review": true,
  "review_reasons": ["缺少服务单末页", "里程与工单相差 8.2%（识别 168，工单 182），超过 5%"],
  "vin": {"license": "HLC...", "frame": "HLC...", "match": true, "confidence": "full"},
  "mileage": {"odometer": 168, "expected": 182, "deviation": 0.082, "ok": false},
  "plate": "晋D08906D",
  "oil": {"axle_actions": ["drain", "fill"], "axle_label": "放油+加油"},
  "filter": {"state": "has"},
  "service_sign": {"ok": false, "present": false, "reasons": []},
  "scenes": ["arrival_plate", "license_vin", "frame_vin", "odometer"],
  "missing_scenes": ["service_sign"],
  "photos": [],
  "latency_ms": 1680
}
```

**业务系统只需要看 `auto_approved`**：`true` 自动通过；`false` 转人工，
原因在 `review_reasons`，缺的图在 `missing_scenes`。

---

## 5. 稳定原因码（`codes`）

| 码 | 含义 | 建议处理 |
|---|---|---|
| `MISSING_PLATE` | 未识别到车牌 | 重拍/人工录入；不阻断整单（按规格跳过） |
| `MISSING_LICENSE_VIN` | 交了行驶证但没读到 VIN | 重拍或人工录入 |
| `MISSING_FRAME_VIN` | 交了车架铭牌但没读到 VIN | 重拍或人工录入 |
| `VIN_CHECKSUM_FAILED` | VIN 校验位不通过 | 人工核对，不能自动放行 |
| `VIN_MISMATCH` | 行驶证与车架 VIN 不一致 | 人工核对 |
| `PLATE_MISMATCH` | 车牌与工单不一致 | 人工核对 |
| `ODOMETER_MISMATCH` | 里程与工单偏差 > 5% | 人工核对 |
| `MISSING_ODOMETER` | 交了里程表但没读到数字 | 重拍或人工录入 |
| `MISSING_DRAIN_PHOTO` / `MISSING_FILL_PHOTO` | 非汉德桥缺放油/加油照片 | 补拍 |
| `RULE_HANDE_DRAIN` / `RULE_HANDE_TIRE` | 汉德桥出现放油/轮胎判定 | 业务规则冲突，人工确认 |
| `MISSING_SIGNATURE` / `MISSING_STAMP` | 服务单末页缺签字/盖章 | 补拍 |
| `OCR_TIMEOUT` / `OCR_UNAVAILABLE` | OCR 超时/不可用 | 重试；仍失败转人工 |
| `TIMEOUT` | 单图分析超时 | 重试或减少张数 |
| `INVALID_PARAMS` | 参数错误 | 修参数，不要重试 |
| `UNAUTHORIZED` | 缺/错 API Key | 检查请求头 `X-API-Key` |
| `RATE_LIMITED` | 触发限流 | 退避重试 |
| `INTERNAL_ERROR` | 服务内部错误 | 带 `request_id` 找平台排查 |

HTTP 错误统一格式：

```json
{"code": "INVALID_PARAMS", "message": "参数校验失败", "request_id": "...", "path": "/v1/arrival"}
```

---

## 6. 认证、限制与重试

- 认证：部署时设置了 `API_KEYS`，所有 `/v1/*` 请求需带 `X-API-Key: <key>`；未设置则内网直连。
- 限制（以 `/v1/meta` 为准）：单张 ≤ 15MB、整单 ≤ 80MB、最多 20 张、
  并发 8、默认限流 120 次/分钟。
- 超时：单图硬超时默认 5–8 秒；整单会逐张处理，接口本身最多约 20 秒。
- 重试：网络超时/5xx 可重试，建议退避 1s/2s/4s，最多 3 次；
  `INVALID_PARAMS` / `UNAUTHORIZED` 不要重试。
- 追踪：建议每个请求带 `X-Request-Id`（UUID），服务会原样回传并在错误里返回。

---

## 7. 本地验证脚本（可直接发给开发）

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://10.51.13.7:8020}"
IMG="${2:?用法: ./check.sh http://服务器:8020 图片路径}"

echo "== meta =="
curl -fsS "$BASE/v1/meta" | jq '{version, model: .model.configured_version, limits}'

echo "== health =="
curl -fsS "$BASE/healthz"; echo
curl -fsS "$BASE/readyz" | jq '{ok, vision, ocr}'

echo "== analyze =="
curl -fsS -X POST "$BASE/v1/analyze" -F "image=@$IMG" \
  | jq '{scene, scene_confidence, fields, need_review, review_reasons}'

echo "== arrival =="
curl -fsS -X POST "$BASE/v1/arrival" -F "images=@$IMG" \
  | jq '{ok, complete, status, plate, missing_scenes, review_reasons}'
```

---

## 8. 推荐接入流程

1. 工单收齐照片后，按业务分类调用 `/v1/workorder`（最省事，跨图规则一次算完）。
2. 如果业务系统是“拍一张传一张”的实时场景，调用 `/v1/scan` 并带 `category`；
   单张结果只用于即时提示，最终以收齐后的 `/v1/workorder` 为准。
3. 只把 `auto_approved=true` 的工单自动放行；其余进入人工复核，
   把 `review_reasons` 原样展示给复核员即可。
4. 每次上线新模型后，先跑 `/v1/meta` 确认 `model.configured_version` 与
   `/readyz.vision.model_version` 一致，再放量。
