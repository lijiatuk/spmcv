PY := conda run --no-capture-output -n spmcv python
DATA := data/processed/v5
RAW  := data/raw/recut

.PHONY: install data heads eval eval-oof eval-aux-oof train-scene-candidate eval-recognize labels-preview bench serve lan test up down upgrade models models-verify contract-check package update fmt

install:
	$(PY) -m pip install -r requirements.txt

## 从原始素材重建数据集（六桶划分 + 类分层 5 折）
data:
	$(PY) scripts/build_dataset_v3.py --source "$(RAW)" --out $(DATA) \
		--accept-assist data/processed/v3/assist_labels.csv --force

## 从场景集派生 group / action / binary 头数据集
heads:
	$(PY) scripts/remap_heads.py --src $(DATA)/cls/fold0 --out $(DATA)/heads --force

## 场景头在 fold-0 上的逐类指标
eval:
	$(PY) scripts/eval_heads.py --weights models/current/weights/scene.pt \
		--data $(DATA)/cls/fold0 --out docs/reports/eval_scene.json

## 5 折严格 OOF：校验工单隔离并输出逐类/逐图指标与权重指纹
eval-oof:
	$(PY) scripts/eval_oof.py --out docs/reports/verified_oof.json

## 完整辅助头候选的工单分组折外评估；只生成报告，不改线上版本
eval-aux-oof:
	$(PY) scripts/eval_union_aux_oof.py --manifest var/runs/union_aux_candidate_0-1-2-3-4.json

## 可复现训练 12 类场景候选；输出仅在 var/runs，不切换 current
train-scene-candidate:
	$(PY) scripts/train_union_scene_candidate.py $(ARGS)

## 预览人工标注形成的改标计划；真正搬动数据仍需显式传 --apply
labels-preview:
	$(PY) scripts/apply_relabel.py

## 单张识别压测：make bench ARGS='--scene arrival_plate --image a.jpg --vus 32 --requests 320'
bench:
	$(PY) scripts/bench_api.py $(ARGS)

## 12 场景识别评测（规格样例）：make eval-recognize BASE=http://127.0.0.1:8020
eval-recognize:
	$(PY) scripts/eval_recognize.py --base "$${BASE:-http://127.0.0.1:8020}" \
		--spec-dir data/external/spec --out docs/reports/eval_recognize.json

serve:
	bash scripts/serve_dev.sh

## 局域网可访问（给别人用 IP 验收）
lan:
	HOST=0.0.0.0 bash scripts/serve_dev.sh

test:
	$(PY) -m pytest tests -q

## 接口契约检查：对运行中的服务验证对外契约（上层接入前必跑）
contract-check:
	$(PY) scripts/contract_check.py --base "$${BASE:-http://127.0.0.1:8020}" \
		--scene "$${SCENE:-arrival_plate}" --photo "$${PHOTO:?用法: make contract-check PHOTO=路径}"

## 浏览器回归：真实 Chrome 点一遍控制台（需要服务已启动）
ui-test:
	node tools/ui_smoke.mjs

## 单元测试 + 浏览器回归
test-all: test ui-test

models:
	$(PY) scripts/models.py list

models-verify:
	$(PY) scripts/models.py verify

up:
	docker compose -f deploy/docker-compose.yml up -d --build

## 一条命令升级（校验 + 构建 + 健康检查）
upgrade:
	bash deploy/upgrade.sh

down:
	docker compose -f deploy/docker-compose.yml down

fmt:
	$(PY) -m ruff check app scripts tests --fix
