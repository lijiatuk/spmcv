from __future__ import annotations

import json

import httpx


class OCRClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(limits=httpx.Limits(
            max_connections=32, max_keepalive_connections=16, keepalive_expiry=20.0))

    def extract(self, data: bytes, filename: str, keep: str,
                box=None, require_vin_label: bool = False) -> dict:
        fields = {"keep": keep}
        if box:
            fields["box"] = json.dumps([float(v) for v in box])
        if require_vin_label:
            fields["require_vin_label"] = "true"
        resp = self.client.post(
            f"{self.base_url}/v1/extract",
            files={"image": (filename, data, "image/png")},
            data=fields,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
