from __future__ import annotations

import httpx

from app.config import settings


class KevJudge:
    def __init__(self, url: str | None = None, mode: str | None = None):
        self.url = url or settings.kev_url
        self.mode = (mode or settings.kev_mode).strip().lower()
        self.enabled = self.mode != "off"

    def _post(self, state, questions) -> dict:
        resp = httpx.post(
            self.url,
            json={"model": "kev-latest", "state": state, "questions": questions},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def arbitrate_oil(self, evidence: dict) -> dict | None:
        if not self.enabled:
            return None
        state = {
            "scene": "桥/轮胎油液作业",
            "fill_score": evidence.get("fill"),
            "drain_score": evidence.get("drain"),
            "detected": evidence.get("detector_labels") or [],
            "ocr_text": (evidence.get("ocr_text") or "")[:200],
        }
        data = self._post(state, {"q": {
            "type": "choice",
            "instructions": "基于给出的视觉证据分数和检测标签，这张照片是放油、加油，还是证据不足？",
            "criteria": {
                "drain": "有油柱下流/接油盆/放油螺栓，且没有加油枪/加油管/油桶。",
                "fill": "有加油枪/加油管/油桶，且没有放油证据。",
                "unknown": "两侧证据冲突、都缺失，或分数都在中间。",
            },
        }})
        ans = (data.get("answers") or {}).get("q") or {}
        return {"action": ans.get("choice"), "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities")}

    def health(self) -> dict:
        # 关闭时不要发请求：KEV 在内网，换台机器就不可达，
        # 每次健康检查白等 5 秒是之前容器反复重启的直接原因之一。
        if not self.enabled or not self.url:
            return {"ok": True, "disabled": True}
        try:
            base = self.url.replace("/v1/systemone", "/v1/models")
            r = httpx.get(base, timeout=5)
            r.raise_for_status()
            return {"ok": True, "models": r.json().get("models", [])}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
