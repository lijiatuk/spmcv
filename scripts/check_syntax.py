#!/usr/bin/env python3
"""提交前语法检查：编译 app/ scripts/ tests/ 下所有 .py。

存在的意义：本轮我有两次把缩进改坏（`IndentationError`），
而服务是分进程启动的，坏掉的那个进程只是不响应，接口静默降级，
看起来像"识别率突然变 0"。这个脚本 10ms 就能挡住。
"""
import compileall
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ok = True
for d in ("app", "scripts", "tests"):
    if not compileall.compile_dir(ROOT / d, quiet=1, force=True):
        ok = False
print("语法检查通过 ✅" if ok else "存在语法错误 ❌")
sys.exit(0 if ok else 1)
