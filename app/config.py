from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> parents[0]=app, parents[1]=repo root
BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    app_name: str = "SPM Vision API"
    version: str = "2.0"
    api_prefix: str = "/v1"

    vision_url: str = "http://127.0.0.1:8016"
    ocr_service_url: str = "http://127.0.0.1:8018"
    # KEV / VLX 默认关闭：它们在内网，换台机器就不可达。
    # 默认 fallback/可达地址会让健康检查每次都等超时。
    kev_url: str = ""
    kev_mode: str = "off"
    vlx_url: str = ""

    use_vlx: bool = False
    max_concurrent: int = 4
    # 分级超时：120s 一把梭会让一个慢 OCR 挂满连接池
    scene_timeout: float = 5.0
    detect_timeout: float = 10.0
    ocr_timeout: float = 15.0
    service_timeout: float = 20.0       # 兜底
    readiness_timeout: float = 2.0      # /readyz 探测下游

    # 上传限制
    max_image_bytes: int = 15 * 1024 * 1024
    max_total_bytes: int = 80 * 1024 * 1024
    max_pixels: int = 50_000_000
    max_photos: int = 20
    # 这个张数以内逐张串行分析，保证结果可复现；超过才并发。
    # 实测并发会让 OCR 互相抢线程，出现「同样输入时好时坏」。
    sequential_photos: int = 6
    # 准入控制：并发满了快速返回 429，而不是排队到超时
    admission_wait_s: float = 2.0
    rate_limit_per_min: int = 120

    # 融合开关：关掉后只用 12 类场景头的结论，不做分组覆盖与动作头纠偏。
    # 用于 A/B——怀疑融合在部分场景上把对的改错了。
    # Candidate v5.1 combines a new scene head with old attribute heads.
    # Fusion must be explicitly enabled after compatible heads are validated.
    enable_fusion: bool = False
    preprocess: str = "auto"
    odometer_vin_fallback: bool = True  # auto | always | off
    min_scene_conf: float = 0.5
    strong_scene_conf: float = 0.85
    # 0.70 → 0.75：fold-0 阈值扫描显示，0.70 时有 1 张静默错判，0.75 起为 0
    weak_scene_conf: float = 0.75

    # 对外服务的 API Key，逗号分隔。为空则不校验（仅限内网演示）。
    # 单图分析硬超时（秒）。实时场景宁可降级返回 review，也不让上层干等。
    # 单图分析的**安全上限**（不是目标延迟）。
    # 实测：单张行驶证 2.7s；两张并发互相抢 OCR 会到 5s+，所以上限给到 8s，
    # 否则多图请求会整批降级成 TIMEOUT。产品目标是 P95 ≤ 2s，
    # 那个要在 L20 上用 bench_api --max-p95-ms 2.0 实测后再收紧。
    request_deadline_s: float = 8.0

    api_keys: str = ""

    # ---- 统一 SSO（DEEPWAY）----
    # off  = 不校验（默认，保持演示可用）
    # mock = 本地联调，任意 Token 通过
    # live = 生产，向用户中心校验
    sso_mode: str = "off"
    sso_open_login_domain: str = ""   # 如 //ihome.deepway-inc.com
    sso_app_code: str = ""            # 每环境单独申请
    sso_pool_code: str = ""           # 每环境单独申请
    sso_user_base: str = ""           # /idaasweb 对应的用户中心网关

    web_dir: Path = BASE_DIR / "web"   # 前端独立目录，与 app 同级
    config_path: Path = BASE_DIR / "app" / "scenes.json"


settings = Settings()
