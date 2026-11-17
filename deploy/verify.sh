#!/usr/bin/env bash
# 服务器部署验收脚本：在服务器上、部署完成后跑一次。
#   bash deploy/verify.sh [http://127.0.0.1:8080] [样例图片路径]
set -uo pipefail
BASE="${1:-http://127.0.0.1:8080}"
IMG="${2:-}"
fail=0

say() { printf "%-42s %s\n" "$1" "$2"; }
check() { # name actual expected
  if [ "$2" = "$3" ]; then say "$1" "✅ $2"; else say "$1" "❌ 期望 $3，实际 $2"; fail=$((fail+1)); fi
}

code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/healthz"); check "健康检查 /healthz" "$code" "200"

ready=$(curl -s -m 10 "$BASE/readyz")
echo "$ready" | grep -q '"ok":true' && say "就绪检查 /readyz" "✅ ok" || { say "就绪检查 /readyz" "❌ $ready"; fail=$((fail+1)); }
ver=$(echo "$ready" | python3 -c "import sys,json;print(json.load(sys.stdin).get('vision',{}).get('model_version',''))" 2>/dev/null)
say "运行中模型版本" "${ver:-❌ 未取到}"
[ -n "$ver" ] || fail=$((fail+1))

contract=$(curl -s -m 10 "$BASE/v1/meta" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('contract',{}).get('api_version',''))" 2>/dev/null)
check "接口契约版本" "${contract:-none}" "v1"

if [ -n "$IMG" ] && [ -f "$IMG" ]; then
  out=$(curl -s -m 120 -X POST "$BASE/v1/scan" -F "images=@$IMG" -F "category=arrival")
  echo "$out" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('  识别结果: decision=%s plate=%s codes=%s' % (d.get('decision'), d.get('plate'), (d.get('codes') or [])[:2]))
assert d.get('decision') in ('pass','review'), 'decision 异常'
assert not d.get('degraded'), '返回降级（TIMEOUT），检查 REQUEST_DEADLINE_S 或 GPU'
" || fail=$((fail+1))
  say "端到端识别 /v1/scan" "$([ $fail -eq 0 ] && echo ✅ || echo ❌)"
else
  say "端到端识别 /v1/scan" "⏭ 未提供样例图片"
fi

# 接口契约（上层接入前必须通过）
if [ -n "$IMG" ] && [ -f "$IMG" ]; then
  if python scripts/contract_check.py --base "$BASE" --photo "$IMG" > /tmp/contract_check.log 2>&1; then
    say "接口契约检查" "✅ 通过"
  else
    say "接口契约检查" "❌ 见 /tmp/contract_check.log"
    fail=$((fail+1))
  fi
fi

echo
[ $fail -eq 0 ] && echo "验收通过 ✅" || echo "验收失败：$fail 项 ❌"
exit $fail
