# 模型训练与发布生命周期

## 1. 冻结数据与折分

每个工单只属于一个验证折。训练前确认 `data/processed/union/splits_group_5fold.json` 完整，所有来源图片可读；新版本训练清单记录折分 SHA 与来源图片清单 SHA。

快速标注的训练改标计划只接收仲裁类别属于 12 类且 `evidence_status=visible` 的记录。`partial`、`not_visible`、`unclear`、`other`、`uncertain` 保留在审计库和导出中，不进入监督分类集。

## 2. 训练候选

```bash
make train-scene-candidate ARGS='--folds 0,1,2,3,4 --epochs 25 --imgsz 512 --batch 8 --device mps'
conda run -n spmcv python scripts/train_union_aux.py \
  --folds 0,1,2,3,4 --epochs 25 --imgsz 512 --batch 8 --device mps
```

权重只写入 `var/runs/`，两个训练流程均不修改 `models/current`。完整辅助头候选用 `scripts/eval_union_aux_oof.py` 按各自验证折评估；场景模型传训练清单给 `scripts/eval_oof.py --manifest <candidate.json>`。候选报告需保留每折、逐类 P/R/F1、混淆、置信度覆盖、模型哈希、折分哈希和来源图片清单哈希。

## 3. 验收候选

先看分组 OOF，再用完全独立、未用于训练或调阈值的完整工单黄金集验收。验收记录应明确每类最小召回/精确率、整单误放行上限、期望覆盖率和复核率。当前项目没有业务方确认的误放行阈值，也没有独立完整工单黄金集，因此已有 OOF 和 fold 0 辅助头结果**不足以开放自动发布**。

## 4. 冻结、激活和回滚

通过验收后冻结权重并绑定报告，按 [部署发布门禁](../deploy/README.md#模型发布门禁) 生成完整 evidence。模型目录不可覆盖；候选必须通过所有证据与哈希检查才可激活。切换后重启 Vision 服务，对照 `/v1/meta.model.configured_version`、`/readyz.vision.model_version` 与每张照片的 `model_version`。若运行版本不一致或接口错误率超阈值，回滚到已校验的 previous/retired 版本。
