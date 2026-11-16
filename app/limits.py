"""上传校验：字节数、真实格式、像素上限。

三个坑都在真实数据里出现过：

1. 产品给的 743 张图里，**643 个文件叫 .png 实际是 JPEG**——所以判断格式
   必须看 magic bytes，不能看扩展名。
2. 手机原图单张 8MB，一个工单 20 张就是 160MB 常驻内存。
3. PNG 解压炸弹可以让 PIL 在解码时吃掉几十 GB 内存。

`Image.MAX_IMAGE_PIXELS` 是 PIL 的全局阀门，导入本模块即生效。
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import settings

# 超过这个像素数 PIL 会抛 DecompressionBombError（而不是 OOM）
Image.MAX_IMAGE_PIXELS = settings.max_pixels

# (magic prefix, 人类可读格式)
_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)


def sniff_image(data: bytes) -> str:
    """按内容判断图片格式；不是已知图片则抛 400。"""
    for prefix, name in _MAGIC:
        if data.startswith(prefix):
            if name == "webp" and data[8:12] != b"WEBP":
                continue
            return name
    raise HTTPException(400, "不是受支持的图片（支持 jpeg / png / webp / tiff）")


async def read_image(upload: UploadFile) -> bytes:
    """读取并校验单张上传图。超限返回 413，格式不对返回 400。"""
    # 多读 1 字节用于判断是否超限，避免把超大文件整个读进内存
    data = await upload.read(settings.max_image_bytes + 1)
    if not data:
        raise HTTPException(400, f"空文件：{upload.filename or '未命名'}")
    if len(data) > settings.max_image_bytes:
        limit_mb = settings.max_image_bytes // (1024 * 1024)
        raise HTTPException(413, f"单张图片超过 {limit_mb}MB：{upload.filename or '未命名'}")
    sniff_image(data)
    return data


async def read_images(uploads: list[UploadFile]) -> list[bytes]:
    """读取一组上传图，同时限制单张与总量。"""
    if len(uploads) > settings.max_photos:
        raise HTTPException(400, f"单个工单最多 {settings.max_photos} 张照片")
    out: list[bytes] = []
    total = 0
    for upload in uploads:
        data = await read_image(upload)
        total += len(data)
        if total > settings.max_total_bytes:
            limit_mb = settings.max_total_bytes // (1024 * 1024)
            raise HTTPException(413, f"本单图片总量超过 {limit_mb}MB")
        out.append(data)
    return out
