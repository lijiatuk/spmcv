# 文档索引

## 对外交付（给上层系统只看这一份）

- [SPM图片ai识别API文档.md](SPM图片ai识别API文档.md)
  - 单张图 + 12 个 `scene` 参数：`POST /v1/recognize`
  - HTTP `200` = 本场景识别成功，`422` = 不成功
  - 响应 `scene` / `message` / `data`；只做识别，不做工单比对
  - 支持图片文件或图片 URL

## 部署与运维

- [部署更新手册.md](部署更新手册.md)：打包、堡垒机上传、服务器更新、回滚（**日常最常用**）
- [上线方案.md](上线方案.md)：升级流程、数据/训练/发布、验收清单
- [部署方案.md](部署方案.md)：四容器架构、前置条件、部署步骤、容量与运维
- [../deploy/README.md](../deploy/README.md)：Docker Compose 直接操作
- [../deploy/runbook.md](../deploy/runbook.md)：故障排查与日常操作

## 方案与决策

- [技术方案-简明版.md](技术方案-简明版.md)：**先读这个**——一页讲清设计、API 怎么用、上线后怎么迭代
- [技术方案-落地与迭代规划.md](技术方案-落地与迭代规划.md)：**总纲**——整体架构、模型与优化、训练/发布/数据闭环、日志排障、接口限流、打包部署、迭代路线
- [reports/final_plan_20261120.md](reports/final_plan_20261120.md)：单图 12 场景最终方案（接口、性能、前端、训练目录、迁移）
- [reports/perf_plan_20261120.md](reports/perf_plan_20261120.md)：3s@32 并发的瓶颈定位与改造清单

## 数据与模型

- [labeling_guide_v1.md](labeling_guide_v1.md)：12 类场景标注规范
- [model_lifecycle.md](model_lifecycle.md)：训练、折分、验收与发布门禁
- [../data/datasets/README.md](../data/datasets/README.md)：训练数据集目录（train/val/test、按工单切分）
- `scripts/dataset` / `scripts/train` / `scripts/model`：训练脚本目录（含旧脚本映射）

## 历史资料

- [reports/](reports/)：历次评测、验收、诊断、方案评审记录
- [archive/](archive/)：已过时文档，仅作追溯
  - `API接口说明.md`：v2.0 旧契约（`ok` / `result`）
  - `api_integration.md`：旧的“一图一请求”接入建议
  - `生产部署方案.md`：2026-11-12 的 v1.1 部署方案
  - `最终方案.md`：2026-11-12 的旧架构路线
  - `上层调用接口文档_v2多张版.md`：多张阶段验收（`/v1/check`）的旧契约，已被单张方案取代
