# 文档索引

## 对外交付（给上层系统只看这一份）

- [上层调用接口文档.md](上层调用接口文档.md)
  - 一个入口 `POST /v1/check` + `scene`
  - HTTP `200` = 满足需求，`422` = 不满足
  - 响应 `scene` / `message` / `missing` / `data`，没有 `ok` / `result` / `codes` / `boxes`

## 部署与运维

- [上线方案.md](上线方案.md)：升级流程、数据/训练/发布、验收清单
- [部署方案.md](部署方案.md)：四容器架构、前置条件、部署步骤、容量与运维
- [../deploy/README.md](../deploy/README.md)：Docker Compose 直接操作
- [../deploy/runbook.md](../deploy/runbook.md)：故障排查与日常操作

## 数据与模型

- [labeling_guide_v1.md](labeling_guide_v1.md)：12 类场景标注规范
- [model_lifecycle.md](model_lifecycle.md)：训练、折分、验收与发布门禁

## 历史资料

- [reports/](reports/)：历次评测、验收、诊断、方案评审记录
- [archive/](archive/)：已过时文档，仅作追溯
  - `API接口说明.md`：v2.0 旧契约（`ok` / `result`）
  - `api_integration.md`：旧的“一图一请求”接入建议
  - `生产部署方案.md`：2026-11-12 的 v1.1 部署方案
  - `最终方案.md`：2026-11-12 的旧架构路线
