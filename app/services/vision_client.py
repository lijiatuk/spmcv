from __future__ import annotations

import httpx


class VisionClient:
    """分类头很快、检测慢，超时要分开配，否则一个慢检测会拖住整个请求预算。"""

    def __init__(self, base_url: str, scene_timeout: float = 5.0,
                 detect_timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.scene_timeout = scene_timeout
        self.detect_timeout = detect_timeout
        self.client = httpx.Client(limits=httpx.Limits(
            max_connections=32, max_keepalive_connections=16, keepalive_expiry=20.0))

    def _post_image(self, path: str, data: bytes, filename: str, fields: dict,
                    timeout: float) -> dict:
        resp = self.client.post(
            f"{self.base_url}{path}",
            files={"image": (filename, data, "image/png")},
            data=fields,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def scene(self, data: bytes, filename: str) -> dict:
        return self._post_image("/v1/scene", data, filename, {}, self.scene_timeout)

    def action(self, data: bytes, filename: str) -> dict:
        return self._post_image("/v1/action", data, filename, {}, self.scene_timeout)

    def group(self, data: bytes, filename: str) -> dict:
        return self._post_image("/v1/group", data, filename, {}, self.scene_timeout)

    def binary(self, data: bytes, filename: str, group: str) -> dict:
        return self._post_image("/v1/binary", data, filename, {"group": group},
                                self.scene_timeout)

    def detect(self, data: bytes, filename: str, text: str,
               conf: float | None = None) -> dict:
        fields = {"text": text, "lang": "zh"}
        # conf 是查询参数不是表单字段（vision_service 里 `conf: float = 0.25`）
        path = f"/v1/detect?conf={conf}" if conf is not None else "/v1/detect"
        return self._post_image(path, data, filename, fields, self.detect_timeout)

    def health(self) -> dict:
        try:
            return self.client.get(f"{self.base_url}/healthz", timeout=5).json()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
