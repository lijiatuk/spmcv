"""Strict field parsing for OCR output (no VLM markup, no guessing)."""
from __future__ import annotations

import re

VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
PLATE_RE = re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                      r"[A-Z][A-Z0-9]{5,6}")
PLATE_REV_RE = re.compile(r"([A-Z0-9]{5,6})"
                          r"([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼])"
                          r"([A-Z])")
ODOM_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,7}")
MARKUP_RE = re.compile(r"<[^>]+>|OBJECTS?|ROWSPAN|COLSPAN|TDTD", re.IGNORECASE)
DATE_RE = re.compile(r"\b20\d{2}[/\-.]\d{1,2}[/\-.]\d{1,2}\b")
YEAR_RE = re.compile(r"\b20\d{2}[/\-.]\d{1,2}\b")


def parse_plate(text: str):
    """Handle OCR that splits a plate across lines or reads it right-to-left."""
    flat = re.sub(r"[^A-Z0-9\u4e00-\u9fff]", "", text.upper())
    hits = PLATE_RE.findall(flat)
    if not hits:
        hits = [prov + letter + digits
                for digits, prov, letter in PLATE_REV_RE.findall(flat)]
    hits = list(dict.fromkeys(hits))
    return (hits[0] if hits else None), hits[:5]


LABEL_RE = re.compile(r"(?:车辆识别代号|车辆识别代码|车架号|车辆VIN|VIN)", re.IGNORECASE)
SERIAL_RE = re.compile(r"出厂编号\s*[:：]?\s*([A-HJ-NPR-Z0-9]{7,9})")


def _vin_valid(v: str) -> bool:
    if len(v) != 17 or MARKUP_RE.search(v):
        return False
    if sum(c.isalpha() for c in v) < 5:
        return False
    trans = {str(i): i for i in range(10)}
    trans.update(dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ",
                          [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9,
                           2, 3, 4, 5, 6, 7, 8, 9])))
    weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
    try:
        total = sum(trans[c] * w for c, w in zip(v, weights))
    except KeyError:
        return False
    check = "X" if total % 11 == 10 else str(total % 11)
    return v[8] == check


TEMPLATE_TOKENS = ("DEEPWAY", "CHILL", "OBJECT", "HVU", "TCU", "AMT", "VAS")


def vin_checksum_ok(v: str) -> bool:
    """ISO 3779 check digit. 用于「是否可信」判定，不作为「是否采纳」门槛。

    实测（2026-11-13，VLX 交叉验证 36 张）：6 个明显错误的值（多来自把
    车架贴牌上的「CERTIFICATE OF VEHICLE…」等正文读成 VIN）checksum 全为
    False，而 5 个真实 VIN checksum 全为 True。也就是说 checksum 能干净地
    把噪声和真值分开，但部分国产商用车 VIN 不遵守校验位，所以只能用来
    「标记复核」，不能用来「直接丢弃」。
    """
    return _vin_valid(v)


def _vin_shape(v: str) -> bool:
    """Structural sanity check independent of the (often unused) check digit.

    Many Chinese commercial VINs do not honour the ISO check digit, so shape
    plus a numeric serial tail is a more reliable filter than the checksum.
    """
    if len(v) != 17 or not VIN_RE.fullmatch(v):
        return False
    if sum(c.isalpha() for c in v) < 5 or sum(c.isdigit() for c in v) < 4:
        return False
    if v[9] in "0IOQUZ":  # model-year code can never be these
        return False
    if sum(c.isdigit() for c in v[-6:]) < 4:  # serial tail is numeric
        return False
    if any(tok in v for tok in TEMPLATE_TOKENS):
        return False
    return True


def _windows(s: str, size: int = 17):
    return [s[i:i + size] for i in range(max(0, len(s) - size + 1))]


VIN_FIX = str.maketrans({"I": "1", "O": "0", "Q": "0"})


def _fix_vin_chars(value: str) -> str:
    """VINs never contain I, O or Q, so OCR reading them is a 1/0 misread."""
    return value.translate(VIN_FIX)


def _label_tail(line: str, start: int) -> str:
    """Take the ASCII alphanumeric run right after the label.

    Stripping every non-ASCII char would glue the value to the next field
    (e.g. "车辆VIN:TA002363车型：2524" must yield "TA002363", not "TA0023632524").
    """
    run = re.match(r"[^\u4e00-\u9fff]*", line[start:]).group(0)
    tail = re.sub(r"[^A-Z0-9]", "", run)
    return _fix_vin_chars(tail)


def _vin_candidates(up: str):
    """Extract VIN candidates anchored on the printed label or a standalone line.

    OCR often splits the VIN across lines (or an occluding overlay), so we also
    try a serial-number reconstruction anchored on 出厂编号. Sliding windows over
    body text are a last resort: they produce template phrases and part numbers.
    """
    scored: dict[str, float] = {}
    serial = SERIAL_RE.search(up)
    serial_no = serial.group(1) if serial else ""
    anchored_prefix = ""
    found_label = False

    def add(value: str, priority: float):
        if _vin_shape(value):
            scored[value] = max(scored.get(value, 0.0), priority)

    for line in up.splitlines():
        compact = _fix_vin_chars(re.sub(r"[^A-Z0-9]", "", line))
        if len(compact) == 17:
            add(compact, 3.0 if _vin_valid(compact) else 2.5)
        m = LABEL_RE.search(line)
        if not m:
            continue
        found_label = True
        tail = _label_tail(line, m.end())
        anchored_prefix = anchored_prefix or tail[:16]
        for w in _windows(tail):
            add(w, 3.0 if _vin_valid(w) else 2.0)

    if anchored_prefix and serial_no:
        joined = anchored_prefix[:9] + serial_no[-8:]
        if len(joined) == 17 and _vin_valid(joined):
            add(joined, 4.0)

    if not found_label:
        compact = re.sub(r"[^A-Z0-9]", "", up)
        for w in _windows(compact):
            add(w, 1.0)

    return [v for v, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


def _vin_partial(up: str):
    """Return the short VIN tail printed on some handwritten service sheets."""
    for line in up.splitlines():
        m = LABEL_RE.search(line)
        if not m:
            continue
        tail = _label_tail(line, m.end())
        digits = sum(c.isdigit() for c in tail)
        letters = sum(c.isalpha() for c in tail)
        # Handwritten service sheets often record only the last 5-8 VIN chars
        # (e.g. "T3067", "TA242"), so accept short tails that still look like
        # a serial fragment rather than requiring a full block.
        looks_serial = (digits >= 3 and letters >= 1) or (digits >= 5 and len(tail) >= 6)
        if 5 <= len(tail) < 17 and looks_serial:
            return tail
    return None


def parse_odometer(text: str):
    """Pick a plausible mileage, excluding range/voltage/power/date numbers."""
    clean = MARKUP_RE.sub(" ", text or "")
    clean = DATE_RE.sub(" ", clean)
    clean = YEAR_RE.sub(" ", clean)
    priority, others = [], []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{3,7})(?![\d.])", clean):
        raw = m.group(1)
        value = int(raw.replace(",", ""))
        if 2020 <= value <= 2030:
            continue
        before = clean[max(0, m.start() - 8):m.start()]
        after = clean[m.end():m.end() + 8]
        blocked = any(k in before for k in
                      ("续航", "剩余", "电压", "功率", "温度", "℃", "kWh", "bar", "%"))
        if blocked:
            continue
        if "km" in after.lower():
            priority.append(value)
        else:
            others.append(value)
    cands = priority or others
    cands = sorted(set(cands))
    if not cands:
        return None, []
    big = [c for c in cands if c >= 1000]
    return (max(big) if big else max(cands)), cands[-5:]


def parse_fields(text: str, keep=None, require_vin_label: bool = False) -> dict:
    out = {"vin": None, "plate": None, "odometer": None, "vin_partial": None,
           "vin_checksum_ok": False,
           "vin_candidates": [], "plate_candidates": [], "odometer_candidates": []}
    if not text:
        return out

    clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    clean = re.sub(r"https?://\S+", " ", clean)
    clean = MARKUP_RE.sub(" ", clean)
    clean = DATE_RE.sub(" ", clean)
    clean = YEAR_RE.sub(" ", clean)
    up = clean.upper()

    labeled = LABEL_RE.search(up)
    if require_vin_label and not labeled:
        vins = []
    else:
        vins = _vin_candidates(up)

    plate, plate_cands = parse_plate(up)

    odometer, cands = parse_odometer(clean)

    out.update({
        "vin": vins[0] if vins else None,
        "vin_candidates": vins[:5],
        "vin_partial": None if vins else _vin_partial(up),
        "plate": plate,
        "plate_candidates": plate_cands,
        "odometer": odometer,
        "odometer_candidates": sorted(set(cands))[-5:],
    })

    vin = out["vin"]
    out["vin_checksum_ok"] = bool(vin) and vin_checksum_ok(vin)

    if keep:
        if "vin" not in keep:
            out["vin"], out["vin_candidates"], out["vin_partial"] = None, [], None
        if "plate" not in keep:
            out["plate"], out["plate_candidates"] = None, []
        if "odometer" not in keep:
            out["odometer"], out["odometer_candidates"] = None, []
    return out
