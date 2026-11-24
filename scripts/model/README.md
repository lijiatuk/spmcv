# 模型管理脚本

| 脚本 | 作用 | 现有对应 |
|---|---|---|
| `register.py` | 候选版本登记（不激活） | `models.py freeze` |
| `activate.py` | 校验 manifest 后切 `models/current` | `models.py activate` |
| `verify.py` | sha256 校验 | `models.py verify` |

发布流程：`train → eval_scene(test) → register → activate`。
`models/` 只放已发布版本，`var/runs/` 放训练中间产物。
