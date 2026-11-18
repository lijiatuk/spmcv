# SPM图片ai识别API文档

## 1. 请求说明

```http
POST http://10.51.13.7:8020/v1/check
Content-Type: multipart/form-data

```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| scene | string | 是 | arrival/ repair/ inspection |
| images | file\[ \] | 是 | 该场景的全部照片 |
| axle\_type | string | 否 | 仅 repair：`hande`\=汉德桥，其它或留空=非汉德桥 |
| expected\_plate | string | 否 | 仅 arrival：工单车牌，传了且识别到才比对 |
| expected\_vin | string | 否 | arrival / inspection：工单 VIN，传了才比对 |
| expected\_odometer | number | 否 | 仅 arrival：工单里程，传了且识别到才比对，误差 >5% 不通过 |
| photo\_type | string | 否 | 仅单张模式：指定一个细分场景，见 3.4；传了就不再要求同场景其它照片 |

## 2. 接口响应

### 2.1响应成功

```json
{
  "scene": "arrival",
  "message": "接车照片满足需求：四类照片齐全，VIN 校验通过",
  "missing": [],
  "data": {
    "plate": "晋D08906D",
    "vin": "HLC3J2VF9TA604594",
    "odometer": 182
  }
}

```

### 2.2 响应失败

```json
{
  "scene": "repair",
  "message": "缺少照片：桥加油",
  "missing": ["桥加油"],
  "data": {
    "axle": "drain",
    "tire": "drain+fill",
    "filter": null
  }
}

```

公共字段：

| 字段 | 说明 |
| --- | --- |
| scene | 本次验收的场景 |
| message | 一句话结论/原因，可直接给现场或业务看 |
| missing | 缺少的照片类型（中文），通过时为空数组 |
| data | 该场景的识别结果，字段见下表；没识别到的值为 null/ false |

### 2.3 data按场景返回

| 场景 | 字段 | 值 |
| --- | --- | --- |
| `arrival` | `plate` | 车牌，未识别为 `null` |
|  | `vin` | 采信的 VIN；两个 VIN 都读到且一致才有值，否则 `null` |
|  | `odometer` | 里程读数，未识别为 `null` |
| `repair` | `axle` | 桥的动作：`drain` / `fill` / `drain+fill` / `null` |
|  | `tire` | 轮胎的动作：`drain` / `fill` / `drain+fill` / `null` |
|  | `filter` | 滤芯状态：`none` / `has` / `none+has` / `null` |
| `inspection` | `vin` | 首页完整 VIN，未读到为 `null` |
|  | `vin_partial` | 只读到尾段时给尾段，否则 `null` |
|  | `signature` | 是否检测到客户签字 |
|  | `stamp` | 是否检测到服务站盖章 |

## 3. 接口明细说明

### 3.1 接车 `scene=arrival`

| 照片/检查 | 判定 |
| --- | --- |
| 车头及车牌 | 必须有车头照片；车牌读不到时**按规格跳过**车牌校验；读到且传了 `expected_plate` 才比对 |
| 行驶证 VIN、车架 VIN | 两张照片都要有；**至少读到一个 VIN**；两个都读到必须一致；传了 `expected_vin` 才与工单比对 |
| 里程表 | 必须有里程表照片；读出里程且传了 `expected_odometer` 才做 ±5% 比对；读不出里程不算失败 |

### 3.2 维修 `scene=repair`

| 桥型 | 必须有的照片 | 不判 |
| --- | --- | --- |
| 非汉德（`axle_type` 留空或其它） | 桥放油、桥加油、轮胎放油、轮胎加油 | 滤芯（上传了也忽略，不判失败） |
| 汉德（`axle_type=hande`） | 桥加油、无滤芯、有滤芯 | 轮胎（上传了也忽略，不判失败） |

### 3.3 检查表 `scene=inspection`

| 照片/检查 | 判定 |
| --- | --- |
| 服务单首页 | 必须有；VIN 要能读到。完整 VIN 直接比对；只有尾段时，传了 `expected_vin` 且尾段能对上才算通过 |
| 服务单末页 | 必须有；客户签字、服务站盖章都要检测到 |
| 工单 VIN | 传了 `expected_vin` 才比对；不传只校验 VIN 是否存在 |

### 3.4 单个细分场景 `photo_type`（可选）

默认不传 `photo_type` 时，接口按整个场景验收，必须一次传齐 3.1–3.3 要求的照片。
传了 `photo_type` 后进入“单个细分场景”模式：只校验这一张照片，不再要求同场景其它照片，`missing` 固定为 `[]`。

| scene | photo_type 可选值 |
| --- | --- |
| `arrival` | `arrival_plate`、`license_vin`、`frame_vin`、`odometer` |
| `repair` | `bridge_drain`、`bridge_fill`、`tire_drain`、`tire_fill`、`filter_none`、`filter_has` |
| `inspection` | `service_front`、`service_sign` |

规则：

- `photo_type` 必须属于当前 `scene`，否则 422。
- 该模式只接收 **1 张** 图片；0 张或大于 1 张返回 400。
- 服务端会校验实际识别类别，和 `photo_type` 不一致时返回 422，并给出 `matched=false`、`actual_scene`。
- 跨图规则（两个 VIN 是否一致、至少读到一个 VIN、整单照片是否齐全）仍由不传 `photo_type` 的整单模式负责。

响应在公共字段外增加：

| 字段 | 说明 |
| --- | --- |
| `photo_type` | 本次校验的细分场景 |
| `matched` | 实际识别类别是否等于 `photo_type` |
| `actual_scene` | 实际识别到的类别；未知为 `null` |
| `applicable` | 当前 `axle_type` 下该照片是否属于必交项；仅 repair 可能出现 `false` |
| `data` | 该细分场景的识别结果，按场景返回对应字段 |

`data` 按 `photo_type` 返回：`arrival_plate` → `plate`；`license_vin` / `frame_vin` → `vin`；`odometer` → `odometer`；`service_front` → `vin` / `vin_partial`；`service_sign` → `signature` / `stamp`；桥/轮胎 → `action`；滤芯 → `state`。

示例：只校验服务单首页

```json
{
  "scene": "inspection",
  "photo_type": "service_front",
  "matched": true,
  "actual_scene": "service_front",
  "message": "服务单首页：已识别 VIN HLC3J2V13TA603182",
  "missing": [],
  "data": {
    "vin": "HLC3J2V13TA603182",
    "vin_partial": null
  }
}
```

传错细分场景：

```json
{
  "scene": "inspection",
  "photo_type": "service_front",
  "matched": false,
  "actual_scene": "service_sign",
  "message": "这张图识别为服务单末页，不是服务单首页",
  "missing": [],
  "data": {
    "signature": true,
    "stamp": true
  }
}
```

`applicable=false` 表示照片本身有效，但当前 `axle_type` 不要求该类型；它仍返回 200，调用方不应把它计入整单必交项。最终是否通过，仍以不传 `photo_type` 的整单请求为准。

单张模式的 200 表示“类型匹配，且这张照片自身没有硬性不通过”。车牌、VIN、里程按整单规格允许读不出的项，单张模式仍返回 200，`data` 对应字段为 `null`；整单是否可用仍由整单模式判断。

## 4. 调用示例

```bash
# 接车
curl -X POST http://10.51.13.7:8020/v1/check \
  -F "scene=arrival" \
  -F "images=@plate.jpg" \
  -F "images=@license.jpg" \
  -F "images=@frame.jpg" \
  -F "images=@odometer.jpg" \
  -F "expected_plate=晋D08906D" \
  -F "expected_vin=HLC3J2VF9TA604594" \
  -F "expected_odometer=182"

# 维修：汉德桥
curl -X POST http://10.51.13.7:8020/v1/check \
  -F "scene=repair" -F "axle_type=hande" \
  -F "images=@axle_fill.jpg" -F "images=@filter_none.jpg" -F "images=@filter_has.jpg"

# 检查表
curl -X POST http://10.51.13.7:8020/v1/check \
  -F "scene=inspection" \
  -F "images=@front.jpg" -F "images=@sign.jpg" \
  -F "expected_vin=HLC3J2V13TA603182"

# 单个细分场景：只校验服务单首页，不要求末页
curl -X POST http://10.51.13.7:8020/v1/check \
  -F "scene=inspection" \
  -F "photo_type=service_front" \
  -F "images=@front.jpg" \
  -F "expected_vin=HLC3J2V13TA603182"

```

## 5. 错误响应

```json
{
  "scene": "arrival",
  "message": "缺少 images 图片",
  "missing": [],
  "data": {}
}

```

| HTTP | 场景 |
| --- | --- |
| 400 | 没传 images、图片为空或格式不支持；单张模式不是 1 张 |
| 413 | 单张 >15MB 或总量 >80MB |
| 422 | 业务不满足（缺照片/校验不通过）、photo_type 不属于 scene、识别类型不一致、参数不合法等 |
| 500 | 内部异常，可重试 |
