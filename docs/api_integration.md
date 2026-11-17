# SPMCV API 上层接入

> **已过时（2026-11-17）**：发给上层的最终版见 [`docs/上层调用接口文档.md`](<上层调用接口文档.md>)。本文“一图一请求”的建议在当前部署下不再适用——实测同一请求传 4 张约 3.6s，按场景一次传完即可。

## 基本约定

- 在线文档：部署后打开 `/docs`；机器可读契约：`/openapi.json`。
- 每个请求可传 `X-Request-Id`（建议用调用方生成的 UUID）；服务会原样回传 `X-Request-Id` 响应头，并在结构化错误中带 `request_id`。
- 配置了 `API_KEYS` 后，服务端调用须传 `X-API-Key`。密钥通过部署环境变量管理，不写进客户端日志或代码库。
- 多图接口用 `multipart/form-data`，同名 `images` 字段可重复。单图接口使用 `image`。
- `/v1/meta` 返回 API/taxonomy 版本、模型清单期望版本与哈希、融合开关和上传/并发限制；`/readyz.vision.model_version` 和每张照片的 `model_version` 表示运行中实际加载的版本，调用方可据此发现“切了 current 但 vision 尚未重启”的版本偏差。
- 业务拒绝/不确定由 `decision`、`need_review`、`reasons`、`codes` 表达；不能把 HTTP 200 解释为业务通过。机器应检查 `decision == "pass"` 或整单的 `auto_approved == true`。

## 推荐调用顺序

单张照片或不完整照片集用统一扫描接口；完整业务单据收齐后，再调用整单校验。

```bash
export SPMCV_BASE="http://127.0.0.1:8017"
export SPMCV_API_KEY="<从部署密钥系统注入>"

curl --fail-with-body -sS "$SPMCV_BASE/v1/meta"

curl --fail-with-body -sS -X POST "$SPMCV_BASE/v1/scan" \
  -H "X-API-Key: $SPMCV_API_KEY" \
  -H "X-Request-Id: order-123-photo-01" \
  -F "images=@./arrival.jpg" \
  -F "category=arrival"

curl --fail-with-body -sS -X POST "$SPMCV_BASE/v1/workorder" \
  -H "X-API-Key: $SPMCV_API_KEY" \
  -H "X-Request-Id: order-123-review-01" \
  -F "work_order_id=order-123" \
  -F "axle_type=hande" \
  -F "expected_vin=车辆 VIN" \
  -F "expected_odometer=123456" \
  -F "images=@./arrival.jpg" \
  -F "images=@./odometer.jpg" \
  -F "images=@./service_sign.jpg"
```

`/v1/scan` 的 `category` 可用 `arrival`、`repair`、`inspection` 或留空；接口返回识别场景、字段、结论、复核原因和稳定原因码。分步骤接口为 `/v1/arrival`、`/v1/repair`、`/v1/inspection`。完整工单 `/v1/workorder` 同时校验跨图 VIN 一致性、里程偏差、必需场景和业务规则，返回 `auto_approved`、`need_review`、`missing_scenes` 与逐图结果。

## 错误与重试

```json
{
  "code": "INVALID_PARAMS",
  "message": "参数校验失败",
  "request_id": "order-123-photo-01",
  "path": "/v1/scan"
}
```

客户端按 HTTP 状态码和稳定 `code` 处理输入错误、认证错误、过载/服务不可用与内部错误。网络超时后只对可安全重放的图片分析请求重试，并复用相同 `X-Request-Id`；标注写入等有副作用的请求应先查询当前记录，避免盲目重试。图像大小、每单张数、并发上限和服务超时以 `/v1/meta` 为准。

## 验收约束

`decision=pass` 是单次接口规则的结果，不等同于模型场景分类准确率或完整工单风险门禁。生产方需用完整工单黄金集定义可接受的漏检/误放行率，保持模型版本与 `/v1/meta` 记录一致，并对端到端 P95、错误率、并发上限和超时进行目标服务器压测。

仓库内提供有界、只上传测试图片的基准工具，可复跑单图或多图的 `/v1/scan`：

```bash
conda run -n spmcv python scripts/bench_api.py \
  --image ./test-arrival.jpg --category arrival \
  --vus 2 --requests 20 --out docs/reports/api_bench_local.json
```

报告区分 HTTP 错误率和业务 `decision`，并记录 P50/P95/P99。它不会写业务数据；本机结果仅用于回归比较，不可替代目标服务器上的容量和 SLA 验收。


## `/v1/scan` 参数

| 字段 | 必填 | 说明 |
|---|---|---|
| `images` | 是 | 1..N 张，同名 `images` 重复传 |
| `category` | 否 | `arrival` / `repair` / `inspection`；留空只做场景识别 |
| `expected_plate` | 否 | 工单车牌。识别到车牌时比对，不一致 → `decision=review` |
| `expected_odometer` | 否 | 工单里程。误差 >5% → `decision=review` |
| `expected_vin` | 否 | 工单 VIN（`inspection` 用） |
| `axle_type` | 否 | `hande` 触发汉德桥规则 |
| `boxes` | 否 | `true` 时附带检测框（供前端可视化，不影响判定） |

## 返回字段

| 字段 | 说明 |
|---|---|
| `decision` | `pass` / `review`。**只有 `pass` 才代表业务通过** |
| `codes` | 机器码数组，调用方按它分支 |
| `reasons` | 中文原因，给人看 |
| `hints` | **不阻断**的提示（如"车牌未识别，已按规格跳过校验"） |
| `degraded` | `true` 表示超时降级，结果不完整 |
| `photos[]` | 每张图的 `scene` / `confidence` / `model_version` / `field_summary` / `ocr_text`（原文截断 400 字，用于排查"没拍到"还是"读错了"） |
| `unexpected[]` | 不属于本类的照片（传错步骤时用） |

## 原因码（稳定契约）

| code | 触发条件 |
|---|---|
| `OCR_TIMEOUT` | OCR 服务超时 |
| `OCR_UNAVAILABLE` | OCR 服务暂不可用 |
| `MISSING_PLATE` | 未识别到车牌 |
| `MISSING_LICENSE_VIN` | 未识别到行驶证 VIN |
| `MISSING_FRAME_VIN` | 未识别到车架 VIN |
| `VIN_CHECKSUM_FAILED` | 行驶证 VIN 校验位不通过 |
| `VIN_MISMATCH` | VIN 不一致 |
| `PLATE_MISMATCH` | 车牌与工单不一致 |
| `ODOMETER_MISMATCH` | 里程与工单相差 |
| `MISSING_DRAIN_PHOTO` | 非汉德桥缺少桥放油 |
| `MISSING_FILL_PHOTO` | 非汉德桥缺少桥加油 |
| `MISSING_ODOMETER` | 未识别到里程 |
| `OIL_VERDICT_MISSING` | 桥的加油/放油未判定 |
| `RULE_HANDE_DRAIN` | 汉德桥不应出现桥放油 |
| `RULE_HANDE_TIRE` | 汉德桥不做轮胎油液判定 |
| `RULE_FILTER_UNEXPECTED` | 非汉德桥不做滤芯判定 |
| `MISSING_SERVICE_VIN` | 服务单首页未识别到 VIN |
| `SERVICE_VIN_MISMATCH` | 服务单 VIN 与预期不一致 |
| `MISSING_SIGNATURE` | 未检测到客户签字 |
| `MISSING_STAMP` | 未检测到服务站盖章 |
| `CATEGORY_REQUIRED` | 未指定 category |
| `TIMEOUT` | 分析超时 |
| `INTERNAL_ERROR` | 分析失败 |
| `OTHER` | 未归类的兜底，出现即说明有新原因没映射，应报 bug |

## 自检

接入前对运行中的服务跑一遍，确认文档与实现一致：

```bash
python scripts/contract_check.py --base http://<host>:8080 --photo ./sample.jpg
```

检查项：错误结构统一、`codes` 无中文、每张图带 `model_version`、`X-Request-Id` 回传、
缺参数返回 422 而不是 500。非 0 退出即契约不符。

## 与规格（SPM系统AI识别.xlsx）的对齐

- **车牌未识别不阻断**：按规格"匹配到车头但未识别车牌 → 跳过车牌校验"，
  只在 `hints` 里提示，不产生 `decision=review`。
- **VIN**：两个 VIN 缺失或不一致 → `VIN_MISMATCH` / `MISSING_*`，不进入自动审核。
- **里程**：与工单误差 >5% → `ODOMETER_MISMATCH`。
- **非汉德桥**：需同时有桥放油和桥加油，缺一 → `MISSING_DRAIN_PHOTO` / `MISSING_FILL_PHOTO`。


## 调用方式建议：**一图一请求**

实测对比（同一张车牌图，本机 CPU）：

| 调用方式 | P50 | P95 | 识别成功率 |
|---|---:|---:|---:|
| **1 张 / 请求，1 并发** | **980 ms** | **1527 ms** | **10/10** |
| 2 张 / 请求，2 并发 | 3053 ms | 3833 ms | 车牌 8/15 |

原因：多图同发时 OCR 线程池互相抢，每个角度都变慢，预算一到就退化成
「只读到半个车牌」——表现为**同样的图时好时坏**。

**所以对外推荐：上层按图逐张调用，自己聚合结果。**

```python
for photo in order.photos:
    r = client.scan([photo], category="arrival")
    results.append(r)
```

好处：

1. 每次请求独占资源，结果稳定（可复现）
2. 单张失败只重试那一张，不用整单重传
3. 服务端无状态，好扩容

代价：N 次往返。但单张 ~1s，5 张串行也就 5s，和一次传 5 张（4–6s 且不稳定）差不多。

### 那跨图校验怎么办

跨图校验（VIN 一致性、四件套齐全、汉德桥规则）需要服务端看到整单，
所以有两条路：

- **A. 用 `/v1/workorder`**：一次传完整工单，服务端做跨图校验并返回 `auto_approved`
- **B. 上层自己聚合**：三张单图结果拿回来后，自己比对 VIN / 判断缺件

简单场景推荐 A；上层已有工单系统、想自己控制审核逻辑就用 B。

## 对外暴露哪几个接口

| 接口 | 用途 | 是否对外 |
|---|---|---|
| `/v1/arrival` | 接车：车牌 / 双 VIN / 里程 | ✅ 推荐 |
| `/v1/repair` | 故障修复：桥 / 轮胎 / 滤芯 | ✅ 推荐 |
| `/v1/inspection` | 检查表：首页 VIN / 签字盖章 | ✅ 推荐 |
| `/v1/workorder` | 整单校验 + 跨图一致性 | ✅ 需要整单结论时 |
| `/v1/scan` | 上面四个的统一入口（`category` 选） | ⭕ 只对接一个端点时用 |
| `/v1/analyze` | 单图调试，不做业务判定 | ❌ 内部用 |

**最简对接：只暴露三个分场景接口**，一图一调，上层按工单聚合。
需要"整单能不能过"时再加 `/v1/workorder`。
