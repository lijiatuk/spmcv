# 训练脚本

| 脚本 | 作用 | 现有对应 |
|---|---|---|
| `train_scene.py` | 12 类分类训练（cuda） | `train_scene_cls.py`、`train_union_scene_candidate.py` |
| `eval_scene.py` | val/test 指标 + 混淆矩阵 | `eval_heads.py`、`eval_scene_cls.py` |
| `export_model.py` | 导出权重 + manifest | `export_onnx.py` |

训练在服务器上跑（独立容器，优先级低于推理），产物写 `var/runs/`，验证通过后再进 `models/`。
