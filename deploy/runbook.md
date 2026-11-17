# 服务器部署手册（Linux + L20）

## 0. 前置

| 项 | 要求 |
|---|---|
| 系统 | Ubuntu 22.04+ / 内核 5.15+ |
| GPU | NVIDIA L20 及以上，驱动 ≥ 535 |
| 容器 | Docker 24+、docker compose v2、nvidia-container-toolkit |
| 磁盘 | ≥ 20 GB（模型 + 数据 + 镜像） |
| 端口 | 8020（对外），8016/8017/8018 仅容器内部 |

```bash
# 验证 GPU 能被容器用
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## 1. 部署

```bash
git clone <repo> /opt/spmcv && cd /opt/spmcv

# 模型与数据（必须存在，不随镜像分发）
#   models/current -> models/<version>   含 7 个权重 + manifest.json
#   data/                                原始照片与数据集

cp .env.example .env      # 按需填 API_KEYS / SSO_*
docker compose -f deploy/docker-compose.yml up -d --build
```

首次构建约 10–20 分钟（vision 基础镜像大）。国内走清华源，海外加
`--build-arg PIP_INDEX=https://pypi.org/simple`。

## 1.5 GPU 与 CPU 的区别（别用错镜像）

| | GPU（生产，默认） | CPU（仅无卡机器/CI） |
|---|---|---|
| 基础镜像 | `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` | `python:3.11-slim` |
| 启动命令 | `docker compose -f deploy/docker-compose.yml up -d` | 加 `-f deploy/docker-compose.cpu.yml` |
| `VISION_DEVICE` | `cuda` | `cpu` |
| 单图耗时 | L20 上目标 P95 ≤ 2s | 慢一个量级，只用于功能验收 |

**有 L20 就走默认配置，不要加 CPU 覆盖文件。**
L20 显存 46GB，本模型各头加起来不到 3GB，与其它任务共用完全够。

确认容器真的用上了卡：

```bash
docker exec -it $(docker compose ps -q vision) nvidia-smi
docker logs $(docker compose ps -q vision) | grep -i device   # 应打印 cuda
```

## 2. 验收（必须全绿才算部署成功）

```bash
curl -s localhost:8020/healthz                     # {"ok":true,...}
curl -s localhost:8020/readyz | jq .               # 三个下游 ok，且 model_version 正确
curl -s localhost:8020/v1/meta | jq .contract      # 契约版本
```

**整栈自检（推荐先跑这个）**：

```bash
SAMPLE_IMAGE=data/<车牌图>.png bash deploy/smoke_compose.sh
```

它会：构建并起栈 → 轮询 `/healthz` → **检查容器内 CUDA 是否真的可用** → 跑验收脚本。

一条命令跑完全部验收：

```bash
bash deploy/verify.sh http://127.0.0.1:8020 data/<任一车牌图>.png
```

它会检查 `/healthz`、`/readyz`、运行中的模型版本、契约版本，并真跑一次 `/v1/scan`，
任何一项不对直接返回非 0。

手工跑一次真实识别（这是唯一能证明"整条链路通了"的检查）：

```bash
curl -s -X POST localhost:8020/v1/scan \
  -F "images=@data/<任一车牌图>.png" -F "category=arrival" | jq '{decision,plate,codes}'
```

期望：`decision` 是 `pass` 或 `review`，`plate` 有值（或 `codes` 里是 `MISSING_LICENSE_VIN` 这类业务码，
而不是 `TIMEOUT`）。

压测（在服务器上做，笔记本数字不作数）：

```bash
python scripts/bench_api.py --path /v1/scan --category arrival \
  --image <车牌图> --vus 2 --requests 40 --max-p95-ms 2000
```

## 3. 模型切换

```bash
# 页面上「模型版本 → 切换」或：
curl -X POST localhost:8020/viz/api/model/activate \
  -H 'Content-Type: application/json' -d '{"version":"v5.1.0"}'
docker compose -f deploy/docker-compose.yml restart vision   # 必须重启才生效
```

切换前会校验 manifest 里每个权重的 sha256，不一致直接拒绝。

## 4. 回滚

```bash
curl -X POST localhost:8020/viz/api/model/activate \
  -H 'Content-Type: application/json' -d '{"version":"v5.0.0"}'
docker compose -f deploy/docker-compose.yml restart vision
```

慢 30 秒，不失数据。

## 5. 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| `/readyz` 里 vision 不 ok | 容器没拿到 GPU / 权重缺失 | `docker logs <vision>`，确认 `manifest 校验通过` |
| 所有请求返回 401 | 开了 `API_KEYS` 但调用方没带 `X-API-Key` | 补头，或清空 `API_KEYS` |
| `/v1/scan` 返回 `TIMEOUT` 且 `degraded:true` | 单图超过 `REQUEST_DEADLINE_S` | 调大该变量，或查 GPU 是否被别的进程占用 |
| 精度和报告差很多 | `models/current` 指向的版本不对 | `/v1/meta` 看 `model.designed_version`，`/readyz` 看实际加载版本 |

## 6. 从平台发起训练

**不需要再登服务器跑脚本。** 「模型版本 → 训练」里：

1. 训练数据选「全部历史数据（自动 70/15/15）」或某个上传批次；
2. 点「开始训练」。平台自动合并、去重、按工单整组切分，生成
   `data/processed/datasets/<名称>/manifest.json`；
3. 训练结束自动在独立测试集上评估，页面显示「测试集准确率」；
4. 注册为候选版本 → 设为线上，热加载生效。

API 镜像默认带 ultralytics（`WITH_TRAIN=1`）。训练设备用 `.env` 的
`TRAIN_DEVICE=auto` 控制，自动选 cuda/mps/cpu。CPU 训练较慢，生产有卡时优先 cuda。

> 1–2 轮的冒烟测试记录不会显示在生产页面；需要清理时点「清理测试记录」。
> 完整的部署/训练/验收流程见 [`docs/上线方案.md`](../docs/上线方案.md)。
