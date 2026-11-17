"""服务单末页：签字低阈值 + 旋转兜底的回归测试。

背景：用户 13 张末页照片里，默认阈值只检出 7 张签字；其中 1 张整页转了 90°，
检测器直接返回空。这两类都是「肉眼明明有签字，接口说没有」。
"""
from __future__ import annotations

import io

from PIL import Image

from app.api import analyze


def _jpeg(width: int = 400, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "JPEG")
    return buf.getvalue()


class _StubVision:
    """按调用顺序返回预设的检测结果，用来模拟原图/旋转图。"""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls = 0

    def detect(self, data, filename, text, conf):  # noqa: ARG002
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def test_low_confidence_signature_counts(monkeypatch):
    monkeypatch.setattr(analyze, "VISION", _StubVision([{"boxes": [
        {"label": "stamp", "score": 0.90, "box": [10, 10, 50, 50]},
        {"label": "signature", "score": 0.12, "box": [20, 20, 60, 40]},
    ]}]))
    boxes, rotated = analyze._service_sign_boxes(_jpeg(), "x.jpg")
    assert rotated is None
    assert any(b["label"] == "signature" and b["score"] >= analyze.SIGN_CONF
               for b in boxes)


def test_below_threshold_signature_is_ignored(monkeypatch):
    monkeypatch.setattr(analyze, "VISION", _StubVision([{"boxes": [
        {"label": "signature", "score": 0.05, "box": [20, 20, 60, 40]},
        {"label": "stamp", "score": 0.90, "box": [10, 10, 50, 50]},
    ]}]))
    boxes, rotated = analyze._service_sign_boxes(_jpeg(), "x.jpg")
    assert rotated is None
    # 弱框可以留在 evidence 里供排查，但不能算「检出签字」
    assert not any(b["label"] == "signature" and b["score"] >= analyze.SIGN_CONF
                   for b in boxes)


def test_rotation_fallback_finds_signature_and_maps_box_back(monkeypatch):
    upright = {"boxes": [{"label": "stamp", "score": 0.95, "box": [10, 10, 50, 50]}]}
    rotated = {"boxes": [
        {"label": "signature", "score": 0.40, "box": [10, 20, 110, 60]},
        {"label": "service_sheet", "score": 0.90, "box": [0, 0, 300, 400]},
    ]}
    monkeypatch.setattr(analyze, "VISION", _StubVision([upright, rotated]))
    boxes, angle = analyze._service_sign_boxes(_jpeg(400, 300), "x.jpg")
    assert angle == 90
    sig = [b for b in boxes if b["label"] == "signature"]
    # 90° 旋转图上的框映射回原图：x = W - y2, y = x1
    assert sig and sig[0]["box"] == [340.0, 10.0, 380.0, 110.0]
    # 旋转图里的 service_sheet 不进 evidence，避免多余框
    assert sum(b["label"] == "service_sheet" for b in boxes) == 0


def test_rotation_merges_labels_found_at_different_angles(monkeypatch):
    """90° 只找到盖章、270° 才找到签字时，两个都要收，不能一命中就停。"""
    upright = {"boxes": [{"label": "service_sheet", "score": 0.90,
                          "box": [0, 0, 10, 10]}]}
    rot90 = {"boxes": [{"label": "stamp", "score": 0.80, "box": [10, 10, 60, 60]}]}
    rot270 = {"boxes": [{"label": "signature", "score": 0.60, "box": [10, 20, 110, 60]}]}
    monkeypatch.setattr(analyze, "VISION", _StubVision([upright, rot90, rot270]))
    boxes, angle = analyze._service_sign_boxes(_jpeg(400, 300), "x.jpg")
    assert angle == 270
    assert {b["label"] for b in boxes} == {"service_sheet", "stamp", "signature"}
