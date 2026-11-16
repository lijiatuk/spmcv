"""Conditional input normalization.

Measured on the held-out split (see docs/reports/preprocess_analysis.md):

* on well exposed photos every enhancement either does nothing or hurts
  (plate OCR 7/8 -> 5/8 with a plain gamma, scene acc -5 points with CLAHE);
* on underexposed / backlit photos a gamma lift recovers most of the loss
  (plate OCR 4/8 -> 7/8, scene acc 65% -> 78%).

So enhancement is applied only when the frame is actually dark. Everything here
is size preserving, which keeps detector box coordinates valid.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

# A frame with mean luminance below this is treated as underexposed.
DARK_MEAN_LUMA = 90.0
# Below this the shadows are crushed and no gamma lift fully recovers them.
SEVERE_MEAN_LUMA = 55.0


def load_image(data: bytes) -> Image.Image:
    """Decode + honour EXIF orientation."""
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def luminance_stats(img: Image.Image) -> dict:
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    return {
        "mean": float(gray.mean()),
        "std": float(gray.std()),
        "p05": float(np.percentile(gray, 5)),
        "p95": float(np.percentile(gray, 95)),
    }


def _gamma(img: Image.Image, g: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32) / 255.0
    out = np.clip(np.power(arr, g) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def plan_gamma(stats: dict) -> float | None:
    """Return the gamma to apply, or None to leave the image untouched."""
    mean = stats.get("mean", 255.0)
    if mean >= DARK_MEAN_LUMA:
        return None
    if mean < SEVERE_MEAN_LUMA:
        return 0.45
    if mean < 72:
        return 0.6
    return 0.8


def normalize(data: bytes, mode: str = "auto") -> tuple[bytes, dict]:
    """Return PNG bytes plus a small report of what was done (and why)."""
    mode = (mode or "auto").strip().lower()
    if mode in ("off", "0", "false", "none"):
        return data, {"applied": False, "reason": "disabled"}

    img = load_image(data)
    stats = luminance_stats(img)
    gamma = plan_gamma(stats)

    if mode in ("always", "1", "true") and gamma is None:
        gamma = 0.85
    if gamma is None:
        return data, {"applied": False, "reason": "well_exposed", **stats}

    out = _gamma(img, gamma)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), {
        "applied": True,
        "gamma": gamma,
        "reason": "severe_underexposure" if stats["mean"] < SEVERE_MEAN_LUMA
                  else "underexposed",
        **stats,
    }