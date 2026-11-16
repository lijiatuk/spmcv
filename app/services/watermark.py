"""去除照片上的「水印相机」色块（地点/时间那个绿框）。

现场照片基本都带这个水印，它有两个害处：
1. 压住正文（行驶证 VIN 那一行常被切到），OCR 读成错字；
2. 水印里的白字本身会被当成正文，产出 "拍摄时间 2026.11.08 16:21:43" 这类噪声。

A/B 实测（25 张行驶证，只改这一项）：VIN 校验位通过率 60% → 68%。

策略：按颜色定位（高绿、低蓝、成块），用 inpaint 补掉。**只在确实检出水印时才动手**，
普通照片零开销；检测不到就原样返回。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

try:  # opencv 是 rapidocr 的依赖，正常都在
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

# 水印绿/黄的判定：G 明显高于 B，且不是荧光笔那种纯绿
_G_MIN = 110
_GB_DIFF = 60
_B_MAX = 110
# 面积门槛（占全图比例）：w 相机的放置块通常 >=2%
_MIN_AREA = 0.02
# 横幅形状与实心度，用于排除绿色车牌/车身
_ASPECT_MIN = 2.0
_ASPECT_MAX = 6.0
_FILL_MIN = 0.75
# 只在下半部找
_BOTTOM_START = 0.45


def detect(img: Image.Image) -> np.ndarray | None:
    """返回水印区域的掩码，没检到返回 None。

    必须严格，否则会误伤**绿色新能源车牌**（实测把车牌当水印抹掉，
    接车通过率从 43% 掉到 5%）。所以只接受：
      - 单个连通块，面积 >= 2% 全图
      - 实心（填充率 >= 0.75，水印是半透明色块，车牌上有大量白字）
      - 横幅形状（宽高比 2:1 ~ 6:1）
      - 位于图像下半部
    """
    if cv2 is None:
        return None
    arr = np.asarray(img.convert("RGB"))
    h, w = arr.shape[:2]
    r, g, b = arr[..., 0].astype(int), arr[..., 1].astype(int), arr[..., 2].astype(int)
    color = ((g > _G_MIN) & (g - b > _GB_DIFF) & (b < _B_MAX)).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(color, 8)
    best = None
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < _MIN_AREA * w * h:
            continue
        if bh == 0 or not (_ASPECT_MIN <= bw / bh <= _ASPECT_MAX):
            continue
        if area / float(bw * bh) < _FILL_MIN:
            continue
        if y < h * _BOTTOM_START:          # 水印在下半部
            continue
        if best is None or area > best[4]:
            best = (x, y, bw, bh, area, i)
    if best is None:
        return None

    mask = (labels == best[5]).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)
    return mask


def strip(img: Image.Image) -> tuple[Image.Image, bool]:
    """抹掉水印，返回 (处理后图, 是否真的处理过)。"""
    mask = detect(img)
    if mask is None:
        return img, False
    arr = np.asarray(img.convert("RGB"))
    cleaned = cv2.inpaint(arr, mask, 5, cv2.INPAINT_TELEA)
    return Image.fromarray(cleaned), True
