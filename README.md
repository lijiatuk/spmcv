# spmcv · SPM 视觉识别

工单照片 → 场景路由 → 检测/属性 → OCR → 规则 → 结构化结论。
前端是 shadcn/ui 风格单页，后端是 FastAPI；可本机跑，也可用 Docker Compose 部署到 GPU 服务器。

## 当前产品状态（2026-11）

- 当前运行版本 `v5.1.0`，场景模型 5 折按工单严格 OOF 为 **86.98% (1,136/1,306)**；新域 **82.21% (624/759)**。这反映当前目录标签的一致性，不等同于人工黄金集准确率或整单通过率。
- API 融合默认关闭。当前辅助头与场景头版本未对齐；新的辅助头只作为候选训练，完成全部折外与整单验收前不激活。
- 快速标注台按可见证据复核，支持 `other/uncertain`、证据/动作属性、备注、撤销、SQLite 审计和 CSV 导出。详细口径见 [`docs/labeling_guide_v1.md`](docs/labeling_guide_v1.md)。
- 上线与日常迭代见 [`docs/上线方案.md`](docs/上线方案.md)；业务系统联调见 [`docs/API接口说明.md`](docs/API接口说明.md)；训练/验收/发布流程见 [`docs/model_lifecycle.md`](docs/model_lifecycle.md)，部署步骤见 [`deploy/README.md`](deploy/README.md)。
- 新旧数据、训练内目录回放、分组 OOF 和完整工单黄金集是不同口径；旧段落中的历史单折/规格图片数字不得当作当前 OOF 或生产 SLA。

## 1. 目录

按 **Cookiecutter Data Science** 的词汇组织（`raw / external / processed`），代码用 **flat layout**
（PyPA 认为服务型项目没必要上 `src/`，少一层）：

```
web/                        前端（独立目录，与 app 同级，可直接换框架）
  console.html/css/js       控制台：总览 / 在线识别 / 模型版本 / 错误样本 / 标注
  sso.js                    统一登录接入（默认关闭）
  simple.html               极简单页（内部排查用）

app/                        服务代码
  main.py                    app factory + 挂载 web/
  config.py metrics.py limits.py obs.py sso.py scenes.json
  api/                       analyze / workorder / categories / health / viz
  services/                  客户端 + 业务 + 两个推理服务（8016 / 8018）

data/                       数据（不进 git）
  raw/recut/                产品最新给的 743 张（六桶划分的输入）
  raw/legacy/               上一版的同一批图（留作对照）
  external/                 规格 Excel + 内嵌样例图（第三方来源）
  processed/v5/             537 张 + 5 折 + cls/ + heads/
  splits/                   工单分组划分

models/                     模型注册表（registry + current 软链 + 版本目录）
docs/                       方案与报告（docs/reports/）
deploy/                     Docker Compose + 三个 Dockerfile + nginx
scripts/                    数据处理 / 训练 / 评测 / 模型管理
tests/                      pytest
var/                        运行产物：runs/ logs/ feedback.sqlite3（不进 git）
attic/                      退役脚本（可回滚，确认无用后再删）
```

> 历史实验产物（30+ 个旧 run、v3 数据集、归档数据）已于 2026-11-16 清理，
> 腾出约 1.5GB。退役脚本在 `attic/scripts/`，需要时再翻。
## 2. 环境

```bash
conda create -n spmcv python=3.11 -y
conda activate spmcv
pip install -r requirements.txt
```

## 3. 数据

- Excel：`data/external/SPM系统AI识别.xlsx`
- 照片：`data/raw/data/raw/recut/`
- 规范：`data/README.md`

```bash
python scripts/prepare_cls_dataset.py \
  --split-json data/splits/group_5fold.json \
  --fold 0 --out data/processed/cls \
  --source "data/raw/legacy" --force
```

fold 0：train 466 / val 117，12 类。

## 4. 训练

```bash
python scripts/train_scene_cls.py \
  --model models/pretrained/yolo11n-cls.pt --data data/processed/cls \
  --epochs 30 --imgsz 512 --batch 8 --device mps --name cls_v1
```

权重：`var/runs/cls_v1/weights/best.pt`。

检测器（需先标框）：

```bash
python scripts/auto_label.py --provider vlx --limit 50 --out var/auto_label
python scripts/train_yolo_det.py --data datasets/spm/data.yaml --device mps
```

## 5. 启动

```bash
VISION_WEIGHTS=var/runs/cls_v1/weights/best.pt \
VISION_DET_WEIGHTS=var/runs/det_v1/weights/best.pt \
VISION_ACTION_WEIGHTS=var/runs/action_v4/weights/best.pt \
VISION_DEVICE=mps VISION_IMGSZ=640 VISION_ACTION_IMGSZ=512 \
  uvicorn app.services.vision_service:app --host 127.0.0.1 --port 8016

OCR_PROVIDER=rapidocr \
  uvicorn app.services.ocr_service:app --host 127.0.0.1 --port 8018

VISION_URL=http://127.0.0.1:8016 \
OCR_SERVICE_URL=http://127.0.0.1:8018 \
KEV_MODE=off USE_VLX=0 \
MAX_CONCURRENT=4 \
  uvicorn app.main:app --host 127.0.0.1 --port 8017
```

前端：`http://127.0.0.1:8017/ui/`

## 6. API

```bash
curl -s http://127.0.0.1:8017/v1/meta
curl -s http://127.0.0.1:8017/metrics
curl -s http://127.0.0.1:8017/v1/analyze \
  -F "image=@/path/to/photo.jpg" \
  -F "axle_type=hande" -F "expected_action=fill" \
  -F "expected_vin=HLC3J2VF9TA604594" -F "expected_odometer=182"
```

**整单校验**（一次上传同一工单的多张照片，按业务规则汇总）：

```bash
curl -s http://127.0.0.1:8017/v1/workorder \
  -F "images=@接车.jpg" -F "images=@行驶证.jpg" -F "images=@车架.jpg" \
  -F "images=@里程.jpg" -F "images=@服务单末页.jpg" \
  -F "work_order_id=IKD2045720261106001" \
  -F "expected_vin=HLC3J2VF0TA605309" \
  -F "expected_odometer=4618" -F "axle_type=hande"
```

返回 `result`（`pass` 才能自动通过）/ `review_reasons` / `vin`（含各场景 VIN 是否一致）/ `mileage`（5% 偏差）/
`oil` / `filter` / `service_sign` / `missing_scenes`，以及每张照片的明细。

实现的是规格 Excel 里的跨图规则：

- 行驶证 VIN 与车架 VIN 必须一致（都缺失或冲突 → 不自动通过）
- 里程与工单偏差 >5% → 复核
- 汉德桥：不得出现桥放油；不做轮胎油液与滤芯判定
- 必须存在接车照片、里程表、服务单末页
- 服务单末页必须有客户签字与服务站盖章

- `/healthz`：liveness，不查下游，恒快返回
- `/readyz`：readiness，探测 vision/ocr，不可达返回 503
**按素材目录分三个接口**（推荐，调用方不用自己判断场景）：

| 接口 | 对应目录 | 返回 |
|---|---|---|
| `POST /v1/arrival` | `01_接车照片` | 车牌、行驶证 VIN、车架 VIN、里程；两个 VIN 必须一致 |
| `POST /v1/repair` | `02_故障及修复照片` | 桥/轮胎 加油或放油、有/无滤芯；`axle_type=hande` 启用汉德桥规则 |
| `POST /v1/inspection` | `03_项目检查表` | 服务单首页 VIN（可仅尾段）、末页签字与盖章 |

三者都接收同类多张照片，返回 `result` / `reasons` / `photos` / `unexpected`（传错类的照片）。
`result` 只有三个取值：`pass`（可自动通过）、`partial`（照片不齐，仅 arrival）、`review`（需人工）。
上层只判这一个字段，不要在客户端重新拼 boolean。

```bash
curl -s http://127.0.0.1:8017/v1/arrival \
  -F "images=@接车.jpg" -F "images=@行驶证.jpg" \
  -F "images=@车架.jpg" -F "images=@里程.jpg"

curl -s http://127.0.0.1:8017/v1/repair \
  -F "images=@桥.jpg" -F "images=@轮胎.jpg" -F "images=@滤芯.jpg" -F "axle_type=hande"

curl -s http://127.0.0.1:8017/v1/inspection \
  -F "images=@服务单首页.jpg" -F "images=@服务单末页.jpg"
```

通用接口仍保留：`/v1/analyze`（单图）、`/v1/workorder`（跨类整单汇总）。

- `/v1/meta`：组件、并发与上传限制
- `/metrics`：按 `image` / `workorder` 分开的请求数、错误、在途、P50/P95
- `/docs`：OpenAPI

上传限制：单图 ≤15MB、单工单 ≤20 张 / 80MB、像素 ≤5000 万，按 magic bytes 判格式（不信扩展名）。

## 7. 给别人验收（局域网）

Mac 上直接跑，不用 Docker：

```bash
make lan        # 等价于 HOST=0.0.0.0 bash scripts/serve_dev.sh
```

启动后会打印局域网地址，例如 `http://10.50.208.121:8017/ui/`，同事同网段直接打开即可。

只有 `api` 绑 `0.0.0.0`，`vision:8016` / `ocr:8018` 仍然只绑 `127.0.0.1`——它们不该被外部直接访问。

> ⚠️ 当前**没有鉴权**（H1 按要求未做）。开放到局域网前请确认只是内部验收，
> 验完就停；不要在没有内网隔离的环境里长期开着。

## 8. 部署到服务器

```bash
bash deploy/package.sh          # 升级包 ~80MB（代码 + 模型，不含 data/）
bash deploy/package.sh --with-data   # 首次部署 ~430MB（带 data/ 照片与划分）
docker compose -f deploy/docker-compose.yml up -d --build
```

`data/` 和 `var/` 只保留目录骨架，照片与训练产物不重复打包：
首次部署用 `--with-data` 或 `rsync -av data/ 服务器:/opt/spmcv/data/` 同步一次，之后升级都发小包。

访问 `http://<服务器IP>:8080/ui/`；Nginx 限流 + 2 API workers + GPU vision + 2 OCR workers。

## 9. 本地 kev

kev（`http://10.51.13.7:8008/v1/systemone`）是 4B pointer head，**只吃文本，图片会被忽略**；
只做油类/证据摘要仲裁，不参与视觉。`KEV_MODE=off|fallback|always`。

## 10. 当前精度（v5，held-out val，107 张，按工单分组 + 类分层）

> 数据已按 2026-11-11 新归类清洗：743 → 537 张。升级为 YOLO26n-cls。
> 完整报告：[`docs/reports/final_report_v5.md`](docs/reports/final_report_v5.md)

| 环节 | v5（YOLO26n） | 旧（YOLO11n） | Δ |
|---|---|---|---|
| 场景 top1（fold-0） | **95.33%**（102/107） | 91.5% | +3.8pt |
| 场景 top1（5 折均值） | **94.60% ± 1.82%**（537 张，每折独立训练） | — | — |
| 场景 top5 | **100%**（5 折全部） | — | 新增 |
| 高置信放行覆盖 | **93.5%**（场景级，阈值 0.85） | 端到端 56.4% | 口径不同 |
| 其中判定正确 | **98.0%** | 100% | 口径不同 |
| 油液分组 3 类 | **95.56%** | 87.7% | +7.9pt |
| 动作 6 类 | **88.89%** | 75.4% | +13.5pt |
| 组内二分类 | **100%**（13/19/13 张，小样本） | — | 新增 |

### 9.1 字段识别（v5 端到端实测，单图）

| 环节 | 实测 |
|---|---|
| 车架 VIN | `LJ1ER4DU3T3304941`，结构校验通过 |
| 行驶证 VIN | `HLC3J2VEXSA603850`，17 位 + 校验通过 |
| 里程表 | `4534 km` / `4601 km`，两单均正确 |
| 车牌 | `晋K05907D`、`晋E01923D`，新能源末位 D 合规 |
| 服务单首页 VIN | 仅尾段 `T3067` → `vin_partial` + 复核（设计如此，不编造） |
| 单图延迟（M1 MPS，单发） | 91–1479 ms |
| 整单（10 图） | 5.4 s，10/10 场景正确，VIN 冲突被规则拦下 |

> 尾延迟的真实问题：vision 和 ocr 各有一把全局 `LOCK`，5 图并发会被串成 20–40 s。
> 这是并发模型问题，不是模型问题，部署前必须按
> [`docs/生产部署方案.md`](docs/生产部署方案.md) 的 T2/T3 改造。

三头级联：**12 类场景头**定场景 → **3 类分组头**（桥/轮胎/滤芯）纠正油液分组 →
**组内 2 分类头**定加油/放油/有滤芯/无滤芯。

### 9.2 历史基线（已作废，仅供追溯）

在**含 23% 错误标签**的旧 583 张数据、YOLO11n 上测得的旧口径数字，不要再用：

| 环节 | 旧值 |
|---|---|
| 场景 top1 | 91.5%（107/117） |
| 端到端自动通过 | 56.4%（66/117） |
| 油类/滤芯动作 | 78.9%（45/57） |
| 旧 12 类头分组 | 75.4% |
| 旧 3 类分组头 | 87.7% |
| 旧 6 分类动作头 | 75.4% |
| 旧 P50 / P95（4 并发，M1） | 592 ms / 7.3 s |

**为什么作废**：新审计发现旧"已分类"的 583 张里，只有 448 张（76.8%）是干净样本，
其余 135 张分别是"不合格/其实是别类/语义待定/根本不是目标场景"。
详见 [`docs/reports/data_v5_audit.md`](docs/reports/data_v5_audit.md)。

**新口径**：`data/processed/v5/splits_group_5fold.json`，94 个工单按类分层切 5 折，
fold-0 的 107 张是模型从未见过的一折，上表以它为准；工单跨集数为 0（已校验）。

### 规格 Excel 内嵌图的端到端结果

`SPM系统AI识别.xlsx` 里的 12 张样例图（`data/external/spec/`）：

| 场景 | 结果 |
|---|---|
| 接车车头/车牌 | ✅ `晋D08906D` |
| 行驶证 VIN | ✅ `HLC3J2VE3TA683182` |
| 车架 VIN | ✅ `HLC3J2VF9TA604594` |
| 里程表 | ✅ `182 km` |
| 桥放油 / 桥加油 | ✅ `drain` / `fill` |
| 轮胎放油 / 轮胎加油 | ✅ `drain` / `fill`（冲突时标复核） |
| 无滤芯 / 有滤芯 | ✅ `none` / `has`（`has` 会先经动作模型交叉确认） |
| 服务单首页 VIN | ⚠️ 仅尾段 + 复核 |
| 服务单末页 | ✅ 检测到签字与盖章 |

> 字段识别失败时返回 `null` + `need_review`，不编造；车牌与 VIN 都过格式/结构校验。

## 11. 前置处理（背光 / 欠曝）

`app/services/preprocess.py` 按亮度**条件触发**曝光校正，不是全局增强：

- `mean ≥ 90` → 不处理（原始字节直接下行，零开销）
- `mean 72–90` → gamma 0.8；`55–72` → 0.6；`<55` → 0.45
- 只做尺寸不变的 gamma，检测框坐标仍有效
- 开关 `PREPROCESS=auto|always|off`，响应里带 `preprocess` 说明是否处理

A/B 实测（同一服务只切开关）：正常照片**零回归**，模拟背光照片场景 76.1% → **85.5%**、车牌 3/11 → **6/11**。
开销 32 ms/张，val 中仅 8.5% 的图触发。详见 `docs/reports/preprocess_analysis.md`。

## 12. 运行模式与限制

- 默认 `USE_VLX=0`：场景分类 + 6 类动作模型 + 检测器 + OCR + 规则，本地闭环。
- 末页签字/盖章另有**兜底**：检测器缺项时调 VLX（`VLX_URL` 可达且 `VLX_SIGN_FALLBACK=1`，默认开），
  只补缺的那一项，超时/失败自动忽略；实测 34 张检测器没见过的末页，签字 22/34 → 33/34。
- 油类/滤芯先用场景分类定位，再由动作模型细化；两者冲突时给结论 + 复核。
- 置信度 < 0.85 时仍返回建议动作，但 `need_review=true`，交给人工确认。
- 需要更强证据时可 `USE_VLX=1`：低置信场景调用 VLX ROI（延迟 +2–7 s）。
- 油类/滤芯原始文件夹标签按“工单步骤”而非纯视觉，标签有噪声；已用规格样例做困难样本回流。
- 车牌、服务单 VIN 受拍摄分辨率限制；失败时明确复核，不猜测。
- 可选工单校验：`expected_vin` / `expected_plate` / `expected_odometer`，里程偏差 >5% 自动复核。

## 13. 部署与验收

```bash
make test                      # 23 个单测
make eval-spec                 # 规格 Excel 的 12 张样例图回归
make eval-val                  # held-out val 抽样评测
make up                        # Docker Compose
curl http://<IP>:8080/healthz
```

`data/external/spec/` 保存了规格 Excel 内嵌图的标注副本，既用于回归评测，也作为困难样本回流训练。

> **生产部署**：完整方案见 [`docs/生产部署方案.md`](docs/生产部署方案.md)（GPU 共享策略、OCR 容量规划、许可证、性能改造、日志、回滚与验收清单）。本节的 `deploy/` 是开发用 compose，不是生产配置。

验收建议：按工单分组抽 200–300 张黄金集，统计各场景字段准确率、自动通过率、误放行率；
P95 目标 < 3 s（单张、4 并发），需要更高吞吐时横向扩 `vision`/`ocr` 副本。


## 目录与清理约定

| 目录 | 内容 | 可否删除 |
|---|---|---|
| `data/raw/` | 原始照片，**唯一不可再生** | ❌ 绝不删 |
| `data/processed/v5/` | 清洗后的数据集与 5 折切分 | ⚠️ 可由 `build_dataset_v3.py` 重建 |
| `data/processed/union/` | 合并数据集切分 | ⚠️ 可由 `build_union.py` 重建 |
| `models/<version>/` | 已发布模型（含 sha256 manifest） | ❌ 需要回滚能力 |
| `var/runs/` | 训练产物 | ✅ 只保留 `models/` 引用到的 |
| `attic/` | 退役脚本 | ✅ 确认无用后可删 |

清理脚本会先核对 `var/runs` 是否被 `models/registry.json` 或 `docs/reports` 引用，
再决定删除，避免把能复现报告的证据删掉。
