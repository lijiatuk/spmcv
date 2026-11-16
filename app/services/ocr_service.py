#!/usr/bin/env python3
"""Unified OCR service.

Providers:
    OCR_PROVIDER=rapidocr -> local RapidOCR (PP-OCRv5 models)，**默认**
    OCR_PROVIDER=remote   -> MinerU /file_parse（仅历史对照，精度明显更低）

Run:
    OCR_PROVIDER=remote OCR_URL=http://10.51.13.7:8199/file_parse \
    uvicorn services.ocr_service:app --host 127.0.0.1 --port 8018
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.limits import read_image
from app.services.fields import parse_fields
from app.services.watermark import strip as strip_watermark

# 默认必须走本地 RapidOCR：实测它比 MinerU 准 3 倍以上，且无网络依赖。
# 设成 remote 只在显式需要时用。
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "rapidocr").strip().lower()
OCR_URL = os.getenv("OCR_URL", "http://10.51.13.7:8199/file_parse")
OCR_BACKEND = os.getenv("OCR_BACKEND", "pipeline")
OCR_TIMEOUT = float(os.getenv("OCR_TIMEOUT", "30"))
OCR_PLATE_SERVER = os.getenv("OCR_PLATE_SERVER", "1").strip() not in ("0", "false", "off")

# 每个线程一套引擎：RapidOCR 的 session 不是线程安全的，但全局一把锁会把
# 所有 OCR 请求串成一条线（实测 5 图工单 40.9s）。改成 thread-local 之后，
# 线程数由 default executor 的 max_workers 兜住，内存可控。
_tl = threading.local()
_ENGINE_ERRORS: list[str] = []


def _build_rapid(server: bool):
    from rapidocr import ModelType, OCRVersion, RapidOCR

    if not server:
        return RapidOCR()
    return RapidOCR(params={
        "Rec.model_type": ModelType.SERVER,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
    })


def _engine(server: bool = False):
    """取当前线程的引擎，没有就建一个。"""
    attr = "server" if server else "default"
    eng = getattr(_tl, attr, None)
    if eng is None:
        try:
            eng = _build_rapid(server)
        except Exception as exc:  # noqa: BLE001
            _ENGINE_ERRORS.append(f"{attr}: {type(exc).__name__}: {exc}")
            raise RuntimeError(f"RapidOCR 初始化失败: {exc}") from exc
        setattr(_tl, attr, eng)
    return eng


def _rapid_available(server: bool = False) -> bool:
    if OCR_PROVIDER != "rapidocr":
        return False
    if server:
        return _server_ok
    return _default_ok


_default_ok = False
_server_ok = False


def _load_rapid():
    """启动时在主线程各建一套，既做 warm-up 也做可用性判定。"""
    global _default_ok, _server_ok
    if OCR_PROVIDER != "rapidocr":
        return
    try:
        _engine(False)
        _default_ok = True
        print("[ocr] RapidOCR loaded", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[ocr] RapidOCR unavailable: {exc}", flush=True)
        return
    if OCR_PLATE_SERVER:
        try:
            _engine(True)
            _server_ok = True
            print("[ocr] PP-OCRv5 server rec loaded for plates", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[ocr] server rec unavailable: {exc}", flush=True)


# 线程池大小 = 并发引擎数。设太大内存会涨（每线程一套模型），
# 设太小又回到串行。默认 4，和 MAX_CONCURRENT 对齐。
# 4 个角度要能同时跑，线程池给到 8，否则后两个角度排不上队
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "8"))


@asynccontextmanager
def _warmup() -> None:
    """跑一次真实推理，把首次调用的初始化开销（onnx 会话、内存池）提前吃掉。

    不做预热的话，服务起来后的第一个请求会慢 2–3 倍，正好撞上 OCR 预算，
    表现成「第一次识别总是失败，重试就好」。
    """
    try:
        from PIL import ImageDraw
        img = Image.new("RGB", (320, 120), "white")
        ImageDraw.Draw(img).text((10, 40), "WARMUP 12345", fill="black")
        buf = io.BytesIO(); img.save(buf, format="PNG")
        _rapid(buf.getvalue(), _engine(False))
        if _rapid_available(server=True):
            _rapid(buf.getvalue(), _engine(True))
        print("[ocr] 预热完成", flush=True)
    except Exception as exc:  # noqa: BLE001 - 预热失败不应阻塞启动
        print(f"[ocr] 预热失败（不影响启动）: {type(exc).__name__}: {exc}", flush=True)


async def lifespan(_: FastAPI):
    _load_rapid()
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=OCR_WORKERS))
    await asyncio.to_thread(_warmup)
    print(f"[ocr] 线程池 {OCR_WORKERS}，provider={OCR_PROVIDER}", flush=True)
    yield


app = FastAPI(title="SPM OCR", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _remote(png: bytes, filename: str = "crop.png") -> str:
    resp = httpx.post(
        OCR_URL,
        files={"files": (filename, png, "image/png")},
        data={"backend": OCR_BACKEND},
        timeout=OCR_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    status_url = data.get("status_url")
    if data.get("status") in ("pending", "processing", "queued") and status_url:
        data = httpx.get(status_url, timeout=15).json()
    if data.get("result_url") and not data.get("results"):
        data = httpx.get(data["result_url"], timeout=15).json()
    import re
    texts = []
    for v in (data.get("results") or {}).values():
        md = v.get("md_content") or ""
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
        md = re.sub(r"https?://\S+", " ", md)
        if md.strip():
            texts.append(md.strip())
    return "\n".join(texts)


def _rapid(png: bytes, engine=None) -> str:
    engine = engine or _engine(False)
    import numpy as np
    img = Image.open(io.BytesIO(png)).convert("RGB")
    result = engine(np.array(img))
    texts = getattr(result, "txts", None) or []
    return "\n".join(t for t in texts if t)


def ocr_text(png: bytes, filename: str = "crop.png") -> str:
    if OCR_PROVIDER == "rapidocr":
        return _rapid(png, _engine(False))
    return _remote(png, filename)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# 车牌最短长度：省份简称(1) + 字母(1) + 5 位序号 = 7；新能源 8 位。
# 只读到 1–3 个字符基本是被裁断的碎片，不能当"命中"，否则并行变体里
# 谁先返回碎片谁就被采信，表现出"同样输入时好时坏"。
PLATE_MIN_LEN = 6


def _has_value(fields: dict, keep: tuple[str, ...]) -> bool:
    for k in keep:
        v = fields.get(k)
        if not v:
            continue
        if k == "plate" and len(str(v)) < PLATE_MIN_LEN:
            continue
        if k == "vin" and len(str(v)) < 11:
            continue
        return True
    return False


def _rotate(img: Image.Image, angle: int) -> Image.Image:
    return img.rotate(angle, expand=True) if angle else img


PROVINCES = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"


def _plate_city(img: Image.Image) -> str | None:
    """OCR just the province+city-letter block (left third of a plate crop)."""
    left = img.crop((0, 0, int(img.width * 0.35), img.height))
    left = ImageEnhance.Contrast(left).enhance(1.8)
    left = left.resize((left.width * 5, left.height * 5), Image.LANCZOS)
    text = ocr_text(_png(left)).upper()
    text = re.sub(r"[^A-Z\u4e00-\u9fff]", "", text)
    if len(text) >= 2 and text[0] in PROVINCES and text[1].isalpha():
        return text[:2]
    return None


def _reconstruct_plate(img: Image.Image, text: str) -> str | None:
    """Rebuild a NEV plate when OCR dropped the city letter (e.g. 晋·08906D)."""
    province = next((c for c in text if c in PROVINCES), None)
    if not province:
        return None
    tail = re.search(r"[^A-Z0-9]*([A-Z0-9]{6})\s*$", text.upper())
    if not tail:
        return None
    head = _plate_city(img)
    if not head or head[0] != province:
        return None
    return head + tail.group(1)


@app.get("/healthz")
def healthz():
    ok = OCR_PROVIDER == "remote" or _default_ok
    return {"ok": ok, "provider": OCR_PROVIDER,
            "remote": OCR_URL if OCR_PROVIDER == "remote" else None,
            "rapidocr": _default_ok,
            "plate_server": _server_ok,
            "engine_errors": _ENGINE_ERRORS[-3:]}


@app.post("/v1/extract")
async def extract(image: UploadFile = File(...), box: str = Form(""),
                  keep: str = Form(""), require_vin_label: bool = Form(False)):
    raw = await read_image(image)
    has_box = bool(box.strip())
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    # 水印色块会压住正文并制造噪声，能定位到就先抹掉（普通照片不会触发）
    img, watermark_removed = strip_watermark(img)
    if box.strip():
        try:
            x1, y1, x2, y2 = [int(float(v)) for v in box.strip("[]").split(",")]
        except Exception:
            raise HTTPException(400, "box must be [x1,y1,x2,y2]")
        img = img.crop((max(0, x1), max(0, y1), min(img.width, x2), min(img.height, y2)))

    # 限制输入长边：OCR 引擎内部本来就会缩放，先把图缩到它实际使用的尺寸，
    # 能省掉一次无效的高分辨率检测。实测 1440→960 快 42%，VIN 结果不变。
    max_side = int(os.getenv("OCR_MAX_SIDE", "960"))
    if max_side and max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                         Image.LANCZOS)

    t_start = time.monotonic()
    keep_tuple = tuple(keep.split(",")) if keep else None
    wanted = keep_tuple or ("vin", "plate", "odometer")
    errors: list[str] = []

    # Photos are frequently rotated, and some phone apps save a mirrored image.
    # Cheap passes run first; expensive ones only when the field is still missing.
    if require_vin_label:
        # Service sheets keep the VIN in the header band; a taller crop only
        # recovered one extra sheet while more than tripling the read time.
        variants = [(0, False, None, 0.55), (90, False, None, 0.55),
                    (270, False, None, 0.55)]
    elif keep_tuple and set(keep_tuple) <= {"vin"}:
        # 行驶证/车架贴牌经常被**横着**拍（旋转 90°/270°），只试 0/180 会直接读空。
        # 实测：一张横躺的行驶证在 0° 全空，旋转 90° 后 VIN 完整可读。
        # attempt() 命中即返回，所以成功的图仍只花 1 次，失败图最多 4 次。
        # 顺序执行，先试最可能的：手机竖拍多为 270° 才转正，
        # 排在第二能少跑两次（实测中位 3.8s → 2.2s）
        variants = [(a, False, None, None) for a in (0, 270, 90, 180)]
    else:
        variants = [(angle, False, None, None) for angle in (0, 270, 90, 180)]
        if keep_tuple and "plate" in keep_tuple:
            variants += [(0, False, "contrast", None), (90, False, "contrast", None)]
            variants += [(0, False, "sharp", None), (90, False, "sharp", None)]
            if has_box:
                variants += [(angle, True, None, None) for angle in (0, 90, 270, 180)]

    def render(frame_base, angle, flipped, enhanced, crop_ratio):
        frame = frame_base
        if crop_ratio:
            frame = frame.crop((0, 0, frame.width, int(frame.height * crop_ratio)))
        frame = _rotate(frame, angle)
        if flipped:
            frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
        if enhanced == "contrast":
            frame = ImageEnhance.Contrast(frame).enhance(1.8)
            if frame.width < 650:
                scale = max(2.0, 650 / frame.width)
                frame = frame.resize((int(frame.width * scale), int(frame.height * scale)),
                                     Image.LANCZOS)
        elif enhanced == "sharp":
            scale = 3 if frame.width < 200 else 2
            frame = frame.resize((frame.width * scale, frame.height * scale), Image.LANCZOS)
            frame = frame.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        elif frame.width < 320:
            # Upscale only genuinely tiny crops; resampling an already-readable
            # crop measurably hurts recognition.
            scale = 2 if frame.width < 200 else 1.5
            frame = frame.resize((int(frame.width * scale), int(frame.height * scale)),
                                 Image.LANCZOS)
        return frame

    # 顺序执行。试过 2 路、4 路并行：CPU 被打满后每个角度都变慢，
    # 预算一到反而把「马上要读出完整车牌」的那个角度砍掉，
    # 成功率比顺序执行更低（实测 4 路并行 8/15，顺序 12/15 以上）。
    # 顺序执行 + 命中即停，是这里最稳的策略。
    PARALLEL = 1
    # 单张 OCR 总预算。调过三次：1.6 太紧（成功就快、稍慢就放弃），
    # 2.5 仍会在难图上打断"已经快读出完整车牌"的变体，退回只剩一个字的碎片，
    # 表现成同样输入时好时坏。给到 4s。
    BUDGET_S = float(os.getenv("OCR_BUDGET_S", "6.0"))

    async def _run_variant(frame_base, variant):
        angle, flipped, enhanced, crop_ratio = variant
        frame = render(frame_base, angle, flipped, enhanced, crop_ratio)
        text = await asyncio.to_thread(ocr_text, _png(frame),
                                       image.filename or "crop.png")
        fields = parse_fields(text, keep=keep_tuple, require_vin_label=require_vin_label)
        return variant, text, fields

    async def _run_phase(frame_base, phase, deadline):
        """并行跑一批变体，命中即取消其余；返回 (hit, fallback)。"""
        hit = None
        fallback = None
        queue = list(phase)
        running: set[asyncio.Task] = set()
        try:
            while queue or running:
                while queue and len(running) < PARALLEL and time.monotonic() < deadline:
                    running.add(asyncio.create_task(_run_variant(frame_base, queue.pop(0))))
                if not running:
                    break
                done, running = await asyncio.wait(
                    running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        variant, text, fields = task.result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{type(exc).__name__}: {exc}")
                        continue
                    # 返回 5 元组 (angle, flipped, enhanced, text, fields)，
                    # 不要用 *variant —— 它含第 4 个元素 crop_ratio，会把位置错开。
                    angle, flipped, enhanced, _crop = variant
                    value = (angle, flipped, enhanced, text, fields)
                    if fallback is None:
                        fallback = value
                    if _has_value(fields, wanted):
                        hit = value
                        return hit, fallback
                    if require_vin_label and sum(c.isalnum() for c in text) >= 120:
                        # 版面读得出来、只是缺完整 VIN（手写尾号常见），别再多花钱
                        hit = value
                        return hit, fallback
        finally:
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
        return hit, fallback

    async def attempt(frame_base):
        """Return (best, fallback) for one crop.

        分两阶段并行：先跑基础角度，没命中再跑增强/镜像变体。
        原来 11 个变体串行，最坏要 5 秒；现在最坏是两批的耗时。
        """
        deadline = time.monotonic() + BUDGET_S
        base = [v for v in variants if v[2] is None and not v[1]]
        extra = [v for v in variants if v not in base]

        hit, fallback = await _run_phase(frame_base, base, deadline)
        if hit or not extra or time.monotonic() >= deadline:
            return hit, fallback

        hit2, fallback2 = await _run_phase(frame_base, extra, deadline)
        return hit2 or None, fallback or fallback2

    best, fallback = await attempt(img)

    if (keep_tuple and "plate" in wanted
            and (best is None or not best[4].get("plate"))):
        base = best or fallback
        if base:
            rebuilt = _reconstruct_plate(img, base[3])
            if rebuilt:
                rebuilt_fields = parse_fields(rebuilt, keep=keep_tuple)
                if rebuilt_fields.get("plate"):
                    best = (0, False, base[2], rebuilt, rebuilt_fields)

    base_text = (best[3] if best else (fallback[3] if fallback else "")) or ""
    seen_chars = sum(c.isalnum() for c in base_text)
    # server 模型是最后手段：3 个尺度串行，每个都要几秒。
    # 加时间闸——已经超预算就不再试，宁可返回 review，也不让单张拖到 8 秒。
    retry_budget = float(os.getenv("OCR_PLATE_RETRY_S", "3.0"))
    if (keep_tuple and "plate" in wanted
            and (best is None or not best[4].get("plate"))
            and _rapid_available(server=True) and seen_chars >= 4
            and (time.monotonic() - t_start) < retry_budget):
        prepared = []
        for contrast, scale in ((1.8, 3), (1.0, 2), (1.0, 3)):
            frame = img if contrast == 1.0 else ImageEnhance.Contrast(img).enhance(contrast)
            prepared.append(frame.resize((frame.width * scale, frame.height * scale),
                                         Image.LANCZOS))
        for frame in prepared:
            if (time.monotonic() - t_start) >= retry_budget:
                break
            try:
                text = _rapid(_png(frame), _engine(True))
                fields = parse_fields(text, keep=keep_tuple)
            except Exception as exc:
                errors.append(f"plate server: {type(exc).__name__}: {exc}")
                break
            if fields.get("plate"):
                best = (0, False, "server", text, fields)
                break

    if best is None:
        if fallback is None:
            return {"text": "", "fields": parse_fields("", keep=keep_tuple),
                    "error": "; ".join(errors), "provider": OCR_PROVIDER,
                    "rotation": None, "flipped": None}
        best = fallback

    angle, flipped, enhanced, text, fields = best
    return {"text": text, "fields": fields, "error": None,
            "provider": OCR_PROVIDER, "rotation": angle, "flipped": flipped,
            "enhanced": enhanced, "watermark_removed": watermark_removed}
