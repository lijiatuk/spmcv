# SPM 图片 AI 识别 API

版本 `3.0` · 单张识别 · 12 个细分场景 · 服务地址 `http://10.51.13.7:8020` · 更新 2026-11-20

> 本文件是**唯一对外接口文档**。旧的 `/v1/arrival`、`/v1/repair`、`/v1/inspection`、
> `/v1/scan`、`/v1/workorder`、`/v1/check`（多张）全部下线或转为内部接口，不在本文范围。

---

## 1. 接口概述

一个接口、一张图、一个场景：

```text
POST /v1/recognize
```

- 传 **1 张图片**（文件或 URL）和 **1 个 `scene` 参数**；
- 判定只看 **HTTP 状态码**：`200` = 本场景识别成功，`422` = 识别不成功；
- 返回 `scene` / `message` / `data`，`data` 按场景给字段；
- 没有 `ok`、`result`、`codes`、`boxes`、`photos[]`。

**只做单张识别，不做工单比对、不做跨图校验。** 工单级规则（VIN 是否一致、四件套是否齐、
汉德桥要哪些照片）由业务系统拿到单张结果后自行组合。

---

## 2. 请求

```http
POST http://10.51.13.7:8020/v1/recognize
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scene` | string | 是 | 12 个场景之一，见第 3 节 |
| `image` | file | 二选一 | 图片文件，jpeg / png / webp / tiff，≤15MB |
| `image_url` | string | 二选一 | 图片 URL，服务端拉取；≤15MB，下载超时 3s |

`image` 与 `image_url` 必须传且只传一个。

---

## 3. 12 个场景与返回字段

| `scene` | 含义 | 200（识别成功）条件 | `data` 字段 |
|---|---|---|---|
| `arrival_plate` | 车头及车牌号 | 检测到车头且读到车牌 | `plate` |
| `license_vin` | 行驶证 VIN | 读到 17 位 VIN | `vin` |
| `frame_vin` | 车架贴牌 VIN | 读到 17 位 VIN | `vin` |
| `odometer` | 里程表 | 读到总里程数字 | `odometer`（number） |
| `bridge_drain` | 桥放油 | 判定为放油 | `action` = `"drain"` |
| `bridge_fill` | 桥加油 | 判定为加油 | `action` = `"fill"` |
| `tire_drain` | 轮胎放油 | 判定为放油 | `action` = `"drain"` |
| `tire_fill` | 轮胎加油 | 判定为加油 | `action` = `"fill"` |
| `filter_none` | 无滤芯 | 判定为无滤芯 | `filter_state` = `"none"` |
| `filter_has` | 有滤芯 | 判定为有滤芯 | `filter_state` = `"has"` |
| `service_front` | 服务单首页 VIN | 读到 VIN，或仅读到尾段且能定位 | `vin`, `vin_partial` |
| `service_sign` | 服务单末页（签字+盖章） | 客户签字与服务站盖章**都**检测到 | `signature`, `stamp` |

**车牌读不到 → 422**（不是 200）。这是与旧版本的关键差异。

### 3.1 响应示例

识别成功（HTTP 200）：

```json
{
  "scene": "arrival_plate",
  "message": "识别成功",
  "data": {"plate": "晋D08906D"}
}
```

```json
{
  "scene": "bridge_fill",
  "message": "识别成功",
  "data": {"action": "fill"}
}
```

```json
{
  "scene": "service_sign",
  "message": "识别成功",
  "data": {"signature": true, "stamp": true}
}
```

识别不成功（HTTP 422）：

```json
{
  "scene": "arrival_plate",
  "message": "未识别到车牌，请重拍",
  "data": {"plate": null}
}
```

```json
{
  "scene": "service_sign",
  "message": "未检测到服务站盖章",
  "data": {"signature": true, "stamp": false}
}
```

```json
{
  "scene": "odometer",
  "message": "识别超时（>5s），请重拍或重试",
  "data": {"odometer": null}
}
```

### 3.2 字段说明

| 字段 | 说明 |
|---|---|
| `scene` | 回显请求的场景 |
| `message` | 一句话结论，可直接展示给现场/业务 |
| `data` | 按场景返回；识别不到的值为 `null` / `false` |

---

## 4. 调用示例

### 4.1 文件上传

```bash
curl -X POST http://10.51.13.7:8020/v1/recognize \
  -F "scene=arrival_plate" \
  -F "image=@plate.jpg"
```

### 4.2 图片 URL

```bash
curl -X POST http://10.51.13.7:8020/v1/recognize \
  -F "scene=license_vin" \
  -F "image_url=https://your-oss.example.com/2026/11/20/xxx.jpg"
```

### 4.3 Python

```python
import requests

def recognize(image_path: str, scene: str) -> dict | None:
    with open(image_path, "rb") as f:
        r = requests.post(
            "http://10.51.13.7:8020/v1/recognize",
            files={"image": f},
            data={"scene": scene},
            timeout=10,
        )
    body = r.json()
    if r.status_code == 200:
        return body["data"]
    return None  # 422：识别失败，展示 body["message"] 让用户重拍
```

---

## 5. 错误响应

所有错误同一结构：

```json
{"scene": "odometer", "message": "识别超时（>5s），请重拍或重试", "data": {"odometer": null}}
```

| HTTP | 含义 | 上层动作 |
|---|---|---|
| 200 | 本场景识别成功 | 使用 `data` |
| 422 | 识别不成功（未识别到目标字段 / 超时） | 提示重拍，展示 `message` |
| 400 | 请求错误（缺 `scene`、缺图、格式不支持、URL 无法下载） | 修请求 |
| 413 | 图片超过 15MB | 压缩后重传 |
| 500 | 服务异常 | 重试 |

---

## 6. 性能与并发

| 项 | 指标 |
|---|---|
| 单张响应时间 | **最大 5s**（硬截止，超时返回 422「识别超时」） |
| 并发 | **目标 32 并发**，准入上限 48，超出返回 429 |
| 单图大小 | ≤15MB |
| 限流 | 单调用方 120 次/分钟（可按需调） |

发生 `429` 时按响应头 `Retry-After` 退避重试。

---

## 7. 与业务系统的配合方式

业务系统按现场流程，每次拍一张、调一次：

```text
接车：车头 → recognize(scene=arrival_plate) → 拿到车牌
      行驶证 → recognize(scene=license_vin)   → 拿到 VIN
      车架   → recognize(scene=frame_vin)     → 拿到 VIN
      里程表 → recognize(scene=odometer)      → 拿到里程
      业务系统自己判断：两个 VIN 是否一致、是否与工单一致

维修：桥放油/桥加油/轮胎放油/轮胎加油/无滤芯/有滤芯
      每张调对应 scene，拿到 action / filter_state

检查表：首页 → recognize(scene=service_front) → 拿到 VIN
        末页 → recognize(scene=service_sign)  → 拿到签字/盖章
```

**本接口不接收工单选填值**（车牌/VIN/里程都不比对）。工单比对在业务系统侧完成。