"""水印去除的单元测试：只在检出水印时动手，普通照片必须原样返回。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from app.services.watermark import detect, strip


def solid(w, h, rgb):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[:, :] = rgb
    return Image.fromarray(a)


def with_watermark(w=400, h=300):
    """右下角画一块水印绿。"""
    a = np.full((h, w, 3), 200, dtype=np.uint8)
    a[int(h * 0.7):, int(w * 0.5):] = (120, 190, 40)
    return Image.fromarray(a)


def test_detects_green_watermark():
    img = with_watermark()
    assert detect(img) is not None
    out, removed = strip(img)
    assert removed is True


def test_plain_photo_untouched():
    img = solid(400, 300, (180, 180, 180))
    assert detect(img) is None
    out, removed = strip(img)
    assert removed is False
    assert out is img


def test_small_green_area_not_treated_as_watermark():
    """绿色车身/广告牌不能被当成水印抹掉。"""
    a = np.full((300, 400, 3), 200, dtype=np.uint8)
    a[10:40, 10:40] = (120, 190, 40)          # 不到 1% 面积
    img = Image.fromarray(a)
    assert detect(img) is None
