# SPMCV 部署

请求路径：`nginx → api → vision + ocr`。API 负责场景路由、OCR、业务规则和整单汇总；vision 在单 GPU 上运行，OCR 使用 RapidOCR。

## 部署前准备

- Docker Compose、NVIDIA 驱动和 `nvidia-container-toolkit`。
- `models/current` 指向完整模型版本，且权重与 `manifest.json` 一致；运行 `make models-verify` 检查。
- 将业务照片挂到 `data/`，评测报告放在 `docs/reports/`。API 容器需要读取数据和报告；改标计划会输出到 `data/processed/relabel_plan.json`。
- `var/` 用作持久目录，快速标注的 SQLite 审计库写在 `var/feedback.sqlite3`。
- 复制 `.env.example` 为 `.env`，配置 SSO、访问 Key 和服务限制。不要把生产密钥提交到版本库。

默认 `ENABLE_FUSION=0`：当前 v5.1.0 的场景头与旧辅助头版本不一致，默认不让旧头覆盖场景判断。只有在兼容辅助头经同一套独立验证后，才将其显式设为 `1`；Compose 会同步控制 API 融合逻辑与 vision 辅助头加载。

## 启动

```bash
docker compose -f deploy/docker-compose.yml config --quiet
docker compose -f deploy/docker-compose.yml up -d --build
```

访问 `http://<服务器IP>:8020/ui/console.html`。API 文档在 `/docs`，业务联调说明见 [`docs/API接口说明.md`](../docs/API接口说明.md)，完整上线流程见 [`docs/上线方案.md`](../docs/上线方案.md)，状态端点是 `/healthz` 和 `/readyz`。

API 容器挂载 `data/`、`docs/`、`models/`、`var/`；其中 **api 的 `models/` 必须可写**（切版本、删版本要写 registry/current），vision 的 `models/` 保持只读。数据与反馈库在容器重建后保留。上传总量上限是 80 MiB，Nginx 留有 multipart 余量。

## 更新（只改前端 / 只改 nginx）

`web/` 是 `COPY web ./web` 打进 api 镜像的，**改前端必须重建 api** 才会生效：

```bash
docker compose -f deploy/docker-compose.yml up -d --build api
```

只改 `deploy/nginx.conf` 时重建 nginx 即可，重建前先验语法：

```bash
docker compose -f deploy/docker-compose.yml exec nginx nginx -t
docker compose -f deploy/docker-compose.yml restart nginx
```

改完确认三件事：

1. `curl -sI localhost:8020/ui/console.css | grep -i content-encoding` 有 `gzip`；
2. `curl -sI localhost:8020/metrics | grep -i content-type` 是 `text/plain`；
3. `curl -s localhost:8020/viz/api/review_queue?family=oil | jq '{total,missing,done}'`
   `missing` 应为 0；不为 0 说明 `data/` 没挂全或路径前缀仍失效。



## 验收

1. `GET /v1/meta` 查看接口能力和限制。
2. 用单图 `/v1/analyze` 或步骤接口 `/v1/arrival`、`/v1/repair`、`/v1/inspection` 验证真实图片。
3. 用完整工单 `/v1/workorder` 验证 VIN、里程、缺图和签章规则；只有完整工单黄金集才能评估整单误放行率。
4. 运行 `make eval-oof` 生成严格工单分组的折外评测；目录回放指标只用于定位样本，不作为泛化验收。
5. 通过 `/metrics` 观察 P50/P95、复核率和错误率，并在目标服务器用目标并发做容量测试。Mac MPS 的延迟不能代替生产 GPU 的 SLA。

## 注意

- `/healthz` 只检查进程，`/readyz` 检查下游。
- SSO 和 API Key 配置通过 Compose 环境注入。启用 SSO 后，按用户中心实际菜单编码配置权限。
- 当前 SQLite 反馈库适用于单机/共享本地卷部署。多副本 API 必须共用支持 SQLite WAL 锁语义的本地文件系统；跨主机水平扩展前应迁至 PostgreSQL。
- 修改改标计划先运行 `python scripts/apply_relabel.py` 预览；确认后显式传 `--apply`。每次文件移动都有撤销日志。

## 模型发布门禁

`models.py freeze` 只登记候选权重，不再支持跳过验收的即时激活。候选版本必须先生成发布验收 JSON，再执行：

```bash
python scripts/models.py activate VERSION --evidence docs/reports/VERSION_release.json
```

版本目录一经创建即视为不可变；即使传入 `--force` 也不会覆盖已有版本，请为新权重分配新的版本号。

验收文件绑定候选版本和 `manifest.json` 的 SHA-256，并将 `ready_to_release` 明确设为 `true`。`acceptance` 必须包含 `grouped_oof`、`golden_set`、`business_risk`、`artifact_integrity` 四项；每项记录 `passed: true`、验收 `target`、实测 `observed`、报告路径及报告 SHA-256。材料缺失、哈希不符或任一项未通过都会拒绝切换。门禁不会替业务方决定风险阈值，验收文件必须明确阈值和实测值。

回滚只允许切到 `previous` 或 `retired` 版本，并先校验权重哈希：

```bash
python scripts/models.py activate PREVIOUS_VERSION --rollback
```

激活后需重启服务并核对 `/v1/meta` 的模型版本与能力开关。当前 v5.1.0 的 OOF 为 86.98%、新域为 82.21%；在独立黄金集和业务误放行门槛通过前，应保持审核优先。


## 已在 macOS 上验证过的部分（2026-11-16）

| 验证项 | 结果 |
|---|---|
| `docker compose config` | 语法通过 |
| `Dockerfile.api` 构建（arm64，走清华源） | 成功 |
| 容器启动 + `/healthz` | `{"ok":true,"version":"2.0"}` |
| 容器连宿主机 vision/ocr，`/readyz` | 通过，`model_version: v5.1.0` |
| 容器内 `/v1/analyze` | 2.7s（宿主机 1.5s，Docker Desktop 跨网开销） |
| vision 镜像（CPU 基础） | ⚠️ 未在本机完成：镜像站拉 torch（483MB）只有 ~370kB/s，20 分钟以上；**请在服务器上构建** |

**vision 镜像无法在 macOS 上构建**：基础镜像 `pytorch/pytorch:*-cuda*` 只有 amd64，
必须在 Linux + NVIDIA 上构建。所以「三服务 compose 一起跑」只能在服务器上验。

## 首次发现的构建缺陷（已修）

`requirements-api.txt` 漏了 `numpy`，容器启动即 `ModuleNotFoundError`。
本机 conda 环境里有 numpy，所以本地怎么跑都正常——**这是"只在本机能跑"的典型陷阱**。
现已固定版本：

```
fastapi==0.115.6  uvicorn==0.32.1  python-multipart==0.0.20
httpx==0.28.1     pydantic==2.10.4 pydantic-settings==2.7.0
pillow==11.0.0    numpy==2.2.1
```

## 构建加速

Dockerfile 默认走清华源，可覆盖：

```bash
docker compose build                     # 默认清华源
docker compose build --build-arg PIP_INDEX=https://pypi.org/simple   # 海外
```

本机开发环境同样配置了清华源（`~/.config/pip/pip.conf`）：

```bash
pip config list
# global.index-url='https://pypi.tuna.tsinghua.edu.cn/simple'
# global.trusted-host='pypi.tuna.tsinghua.edu.cn'
```

`deploy/pip.conf` 是仓库内的参考副本，新机器上照抄到用户配置目录即可。
