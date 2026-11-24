# 数据集脚本

目标布局（迁移完成后）：

| 脚本 | 作用 | 现有对应 |
|---|---|---|
| `import_images.py` | 导入批次 → 去重 → `data/raw/upload_*` | 无（现由 viz 上传接口完成） |
| `split_dataset.py` | 按工单分组 → train/val/test | `build_dataset_v3.py`、`build_union.py` |
| `build_manifest.py` | 生成 `manifest.csv` + `splits.json` | `make_manifest.py` |
| `validate_dataset.py` | 类别/尺寸/损坏校验 | `check_syntax.py` 部分能力 |

迁移期旧脚本保留，Makefile 的 `data` / `heads` 目标逐步指到新脚本。
