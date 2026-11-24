# 退役脚本

这些脚本只针对已下线的多张 / 阶段接口（`/v1/arrival`、`/v1/repair`、`/v1/inspection`、
`/v1/scan`、`/v1/workorder`、`/v1/check`），保留作历史追溯，不再维护。

当前对外接口见 `docs/SPM图片ai识别API文档.md`：
`POST /v1/recognize`（单张 + 12 场景）。

| 脚本 | 原用途 | 替代 |
|---|---|---|
| `eval_categories.py` | 三类接口端到端验证 | `scripts/eval_recognize.py` |
| `eval_endpoint.py` | `/v1/analyze` 契约检查 | `scripts/contract_check.py` |
| `eval_api_scene.py` | `/v1/analyze` 场景精度 | `scripts/eval_recognize.py` |
| `eval_by_folder.py` | 目录回放 | `scripts/eval_recognize.py` |
| `eval_user_batch.py` | 用户批次回放 | `scripts/eval_recognize.py` |
| `simulate_production.py` | 整单模拟 | 已无对应接口 |
