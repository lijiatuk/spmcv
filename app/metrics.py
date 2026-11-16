"""请求/延迟指标。

之前的问题：`/v1/workorder` 只 `end()` 不 `start()`，导致 `in_flight` 永为负、
单图与整单的延迟混在同一个窗口里算 P95。现在按 kind 分开统计：

    image      单图（/v1/analyze，以及整单里每张图）
    workorder  整单（/v1/workorder 一次调用）

`in_flight` 在 `end()` 里夹紧到 >= 0，避免异常路径把计数打成负数。
"""
from __future__ import annotations

import threading
from collections import deque

KINDS = ("image", "workorder")


class Metrics:
    def __init__(self, window: int = 500):
        self._lock = threading.Lock()
        self._latencies = {k: deque(maxlen=window) for k in KINDS}
        self._stats = {
            k: {"requests": 0, "errors": 0, "reviews": 0,
                "latency_ms": 0.0, "in_flight": 0}
            for k in KINDS
        }

    def start(self, kind: str = "image") -> None:
        with self._lock:
            s = self._stats[kind]
            s["requests"] += 1
            s["in_flight"] += 1

    def end(self, latency_ms: float, error: bool = False, review: bool = False,
            kind: str = "image") -> None:
        with self._lock:
            s = self._stats[kind]
            # 夹紧：异常/重复 end 不应把在途数打成负数
            s["in_flight"] = max(0, s["in_flight"] - 1)
            s["latency_ms"] += latency_ms
            self._latencies[kind].append(latency_ms)
            if error:
                s["errors"] += 1
            if review:
                s["reviews"] += 1

    def render(self) -> str:
        lines: list[str] = []
        for kind, snap in self._stats.items():
            with self._lock:
                snap = dict(snap)
                vals = sorted(self._latencies[kind])
            n = snap["requests"]
            avg = snap["latency_ms"] / n if n else 0.0
            p50 = vals[len(vals) // 2] if vals else 0.0
            p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))] if vals else 0.0
            lines += [
                f"# TYPE spm_{kind}_requests_total counter",
                f"spm_{kind}_requests_total {snap['requests']}",
                f"# TYPE spm_{kind}_errors_total counter",
                f"spm_{kind}_errors_total {snap['errors']}",
                f"# TYPE spm_{kind}_reviews_total counter",
                f"spm_{kind}_reviews_total {snap['reviews']}",
                f"# TYPE spm_{kind}_in_flight gauge",
                f"spm_{kind}_in_flight {snap['in_flight']}",
                f"# TYPE spm_{kind}_latency_ms_avg gauge",
                f"spm_{kind}_latency_ms_avg {avg:.2f}",
                f"# TYPE spm_{kind}_latency_ms_p50 gauge",
                f"spm_{kind}_latency_ms_p50 {p50:.2f}",
                f"# TYPE spm_{kind}_latency_ms_p95 gauge",
                f"spm_{kind}_latency_ms_p95 {p95:.2f}",
            ]
        return "\n".join(lines) + "\n"


metrics = Metrics()
