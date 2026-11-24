#!/usr/bin/env bash
# 打发布包（在开发机执行，产物通过堡垒机手动上传）。
#
#   bash deploy/package.sh                    升级包：代码+模型+配置（默认，几十 MB）
#   bash deploy/package.sh --with-data        首次部署全量包：额外带 data/（几百 MB）
#   bash deploy/package.sh --since <git-ref>  增量包：只打该 ref 之后改动的文件
#
# 产物：
#   dist/spmcv-<mode>-v<version>-<stamp>.tar.gz
#
# 上传到服务器后执行：
#   cd /opt/spmcv && bash deploy/update.sh /path/to/<包名>.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_DATA=0
SINCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-data) WITH_DATA=1; shift ;;
    --since) SINCE="${2:?--since 需要 git ref}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

VERSION=$(grep -m1 '^    version: str' app/config.py | sed -E 's/.*"([^"]+)".*/\1/')
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
STAMP=$(date +%Y%m%d_%H%M%S)
MODE=release
[[ $WITH_DATA == 1 ]] && MODE=full
[[ -n "$SINCE" ]] && MODE=incr

mkdir -p dist
NAME="spmcv-${MODE}-v${VERSION}-${STAMP}.tar.gz"
OUT="dist/$NAME"

WORK=$(mktemp -d)
SKELETON="$WORK/skel"
STAGE="$WORK/stage"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$STAGE" "$SKELETON/data" "$SKELETON/var/logs" \
         "$SKELETON/var/runs" "$SKELETON/var/train_jobs"

# macOS 的 xattr（com.apple.quarantine）会让 Linux 解包刷警告：导出时禁用
export COPYFILE_DISABLE=1

EXCLUDES=(
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store'
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='.git'
  --exclude='./models/pretrained'
  --exclude='./var/runs' --exclude='./var/logs' --exclude='./var/train_jobs'
  --exclude='./var/eval' --exclude='./var/backup'
  --exclude='./data/raw' --exclude='./data/processed' --exclude='./data/external'
  --exclude='./tools/node_modules'
)

TARGETS=(
  ./app ./web ./scripts ./deploy ./docs ./models ./tests
  ./Makefile ./README.md ./.env.example ./requirements.txt ./pyproject.toml
)

if [[ -n "$SINCE" ]]; then
  echo "增量范围：${SINCE}（含工作区改动）"
  # 用单 ref 的 diff（含工作区改动），再补未跟踪的新文件
  FILES=()
  while IFS= read -r f; do
    [[ -n "$f" ]] && FILES+=("$f")
  done < <(
    git -c core.quotepath=false diff --name-only "$SINCE" -- \
      app web scripts deploy docs tests models Makefile README.md \
      requirements.txt pyproject.toml 2>/dev/null || true
    git -c core.quotepath=false ls-files --others --exclude-standard -- \
      app web scripts deploy docs tests models 2>/dev/null || true
  )
  if [[ ${#FILES[@]} -gt 0 ]]; then
    # 去重保序（bash 3.2 没有 mapfile / 关联数组）
    FILES=($(printf '%s\n' "${FILES[@]}" | awk '!seen[$0]++'))
  fi
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "自 $SINCE 以来没有改动，不打包。"
    exit 0
  fi
  printf '  %s\n' "${FILES[@]}"
  # 增量包里也要带更新脚本，避免服务器上没有新版 update.sh
  if [[ ! " ${FILES[*]} " =~ " deploy/update.sh " ]]; then
    FILES+=("deploy/update.sh" "deploy/package.sh")
  fi
  tar -cf "$WORK/base.tar" --no-xattrs "${EXCLUDES[@]}" "${FILES[@]}" 2>/dev/null || true
else
  if [[ $WITH_DATA == 1 ]]; then
    tar -cf "$WORK/base.tar" --no-xattrs "${EXCLUDES[@]}" "${TARGETS[@]}" ./data 2>/dev/null || true
  else
    tar -cf "$WORK/base.tar" --no-xattrs "${EXCLUDES[@]}" "${TARGETS[@]}" 2>/dev/null || true
  fi
fi

# 部署调优项：解包后由 update.sh 合并进服务器 .env（不覆盖 GPU_ID / SSO 等本机配置）
ENV_KEYS=(MAX_CONCURRENT RATE_LIMIT_PER_MIN ADMISSION_WAIT_S REQUEST_DEADLINE_S OCR_BUDGET_S OCR_PLATE_RETRY_S)
: > "$STAGE/ENV.OVERRIDES"
for key in "${ENV_KEYS[@]}"; do
  value=$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2-)
  [[ -n "$value" ]] && printf '%s=%s\n' "$key" "$value" >> "$STAGE/ENV.OVERRIDES"
done

# 包内清单：解包后能核对版本、来源与文件列表
{
  echo "package: $NAME"
  echo "mode: $MODE"
  echo "version: $VERSION"
  echo "git: $GIT_SHA"
  echo "built_at: $(date +%Y-%m-%dT%H:%M:%S%z)"
  [[ -n "$SINCE" ]] && echo "since: $SINCE"
  echo "entries:"
  tar -tf "$WORK/base.tar" | sed 's/^/  /'
} > "$STAGE/MANIFEST.txt"

# base.tar 的成员解到 stage/ 根部，与 MANIFEST 一起重新打包；data/var 骨架单独追加
tar -xf "$WORK/base.tar" -C "$STAGE"
COPYFILE_DISABLE=1 tar -cf "$WORK/all.tar" --no-xattrs -C "$STAGE" . -C "$SKELETON" data var
gzip -c "$WORK/all.tar" > "$OUT"

gzip -t "$OUT"

cat <<EOF

包：${OUT}（$(du -h "$OUT" | cut -f1)）
版本：v${VERSION}  git：${GIT_SHA}  模式：${MODE}

上传到服务器后执行：
  cd /opt/spmcv
  bash deploy/update.sh /path/to/${NAME}

只改 app/web/deploy 用默认包；改了 vision/ocr 代码或依赖时加 --all。
EOF