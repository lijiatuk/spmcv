from __future__ import annotations

import httpx


class VLXClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post_image(self, path: str, data: bytes, filename: str, fields: dict) -> dict:
        resp = httpx.post(
            f"{self.base_url}{path}",
            files={"image": (filename, data, "image/png")},
            data=fields,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def detect(self, data: bytes, filename: str, text: str) -> dict:
        return self._post_image("/v1/detect", data, filename,
                                {"text": text, "lang": "zh"})

    def roi(self, data: bytes, filename: str, scene: str) -> dict:
        return self._post_image("/v1/roi", data, filename, {"scene": scene})

    def recognize(self, data: bytes, filename: str, scene: str) -> dict:
        return self._post_image("/v1/recognize", data, filename, {"scene": scene})
