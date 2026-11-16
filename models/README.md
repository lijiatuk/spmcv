# 模型版本管理

权重不进 git，但**版本必须可追溯、可校验、可回滚**。这个目录就是模型注册表。

## 目录约定

```
models/
  registry.json          # 版本索引：状态 / 指标 / 创建时间 / manifest 指纹
  current -> v5.0.0      # 软链，服务默认加载这个（MODEL_DIR=models/current）
  v5.0.0/
    manifest.json        # 每个文件的 sha256 + 来源 + 指标
    weights/*.pt         # 7 个训练权重
    onnx/*.onnx          # 6 个分类头的 ONNX（推理镜像用，可 torch-free）
```

**进 git**：`registry.json`、`*/manifest.json`、`README.md`
**不进 git**：`*/weights/`、`*/onnx/`（体积大，由构建/发布流程投递）

## 七个头

| role | 文件 | 用途 |
|---|---|---|
| `scene` | scene.pt | 12 类场景分类 |
| `group` | group.pt | 3 类油液分组 |
| `action` | action.pt | 6 类动作 |
| `bin_axle` | bin_axle.pt | 桥 加油/放油 |
| `bin_tire` | bin_tire.pt | 轮胎 加油/放油 |
| `bin_filter` | bin_filter.pt | 滤芯 有/无 |
| `det` | det.pt | 18 类检测器 |

## 常用命令

```bash
python scripts/models.py list                  # 看所有版本、指标、当前指向
python scripts/models.py verify                # 校验 current 的 sha256
python scripts/models.py verify --version v5.0.0

# 训练出新候选，冻结成新版本（不自动切换）
python scripts/models.py freeze v5.1.0 --from runs/candidate --metrics reports/metrics_v51.json

# A/B 通过后切换（上一个 active 自动变成 previous，便于回滚）
python scripts/models.py activate v5.1.0

# 清理非 active/previous 的版本
python scripts/models.py prune
```

## 版本状态含义

| 状态 | 含义 | 谁在用 |
|---|---|---|
| `candidate` | 刚冻结，尚未验证 | 没人，等 A/B |
| `active` | `current` 指向它 | 服务 |
| `previous` | 上一个 active | 回滚目标，建议保留 ≥1 个 |
| `archived` | 明确淘汰 | `prune` 会删掉 |

## 服务怎么加载

`app/services/vision_service.py` 的解析优先级：

1. 单个显式 env（`VISION_WEIGHTS` 等）—— 兼容旧部署和临时实验
2. `MODEL_DIR/weights/<role>.pt` —— **推荐**
3. 历史默认路径（`runs/...`）—— 只在本地开发时可能命中

生产只需设一个变量：

```bash
MODEL_DIR=models/current
```

切版本 = 改软链 + 重启容器，**不用改任何环境变量**。

## 回滚

```bash
python scripts/models.py activate v5.0.0   # 软链切回
docker compose restart vision              # 重启生效
```

## 约束

- **权重缺失必须启动失败**，不能静默降级（见 `reports/code_review.md` H4）。当前 `_load_models()` 仍是 print 警告，这项还没修。
- 切版本前先 `verify`，sha256 不一致的版本不要 activate。
- 一个版本里的 7 个头必须**同时产出**——混搭不同版本的 scene/action 会让融合逻辑的阈值失去意义。
