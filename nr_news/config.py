"""Central configuration for the AZAD STUDIO news operation.

No credential value is ever stored here. Secrets are read from environment
variables or Docker/secret-manager mounted files at process start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS = PACKAGE_DIR / "assets"
DATA_DIR = ROOT / "data"
CARD_DIR = DATA_DIR / "cards"
AUDIT_DIR = DATA_DIR / "audit"
MEDIA_DIR = DATA_DIR / "media"
SNAPSHOT_DIR = DATA_DIR / "snapshots"


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret(env_name: str) -> str | None:
    """Read a secret from a file first, then from the environment."""
    file_name = os.getenv(env_name + "_FILE")
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return os.getenv(env_name)


def _first_secret(*names: str) -> str | None:
    for name in names:
        value = _secret(name)
        if value:
            return value
    return None


def _load_dotenv() -> None:
    """Load a local .env only for keys not already supplied by the runtime.

    GitHub Actions, Docker Compose, systemd and secret managers inject real
    environment variables; those always take precedence. The file is ignored by
    git and should contain file paths to secrets, not secret values.
    """
    if os.getenv("NR_SKIP_DOTENV"):
        return
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass(frozen=True)
class Config:
    # Branding — authoritative uploaded asset only.
    logo_path: Path = ASSETS / "nr_logo.png"
    logo_sha256: str = "d0b02c4e7f31f4f58d1e48c08fd3c8d6668bcbbe9e15ef0652a6ff584ede2b3d"
    urdu_font_path: Path = ASSETS / "NotoNastaliqUrdu.ttf"
    latin_font_path: Path = ASSETS / "NotoSans.ttf"

    # Only destination. The numeric value in the profile URL is a lookup hint,
    # never a publishing target until Meta's API confirms it is a managed Page.
    facebook_page_url: str = "https://www.facebook.com/profile.php?id=61595929711037"
    facebook_page_url_hint_id: str = "61595929711037"
    graph_api_version: str = os.getenv("NR_GRAPH_API_VERSION", "v26.0")
    graph_api_base: str = "https://graph.facebook.com"
    expected_page_name: str | None = (os.getenv("NR_EXPECTED_PAGE_NAME")
                                      or os.getenv("FB_PAGE_NAME"))
    facebook_page_id: str | None = (os.getenv("NR_FB_PAGE_ID")
                                    or os.getenv("FB_PAGE_ID"))

    # Paths and timezone.
    db_path: Path = Path(os.getenv("NR_DB_PATH", str(DATA_DIR / "nr_news.sqlite3")))
    data_dir: Path = DATA_DIR
    card_dir: Path = CARD_DIR
    audit_dir: Path = AUDIT_DIR
    media_dir: Path = MEDIA_DIR
    snapshot_dir: Path = SNAPSHOT_DIR
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Kolkata"))

    # Safety defaults.
    dry_run: bool = field(default_factory=lambda: _bool("NR_DRY_RUN", True))
    publish_enabled: bool = field(default_factory=lambda: _bool("NR_PUBLISH_ENABLED", False))
    publish_safety_switch: bool = field(default_factory=lambda: _bool("NR_PUBLISH_SAFETY_OK", False))
    operator_approved: bool = field(default_factory=lambda: _bool("NR_OPERATOR_APPROVED", False))
    verify_approved: bool = field(default_factory=lambda: _bool("NR_VERIFY_APPROVED", False))
    auto_approve_verified: bool = field(default_factory=lambda: _bool("NR_AUTO_APPROVE_VERIFIED", False))
    auto_post_enabled: bool = field(default_factory=lambda: _bool("NR_AUTO_POST_ENABLED", False))
    video_enabled: bool = field(default_factory=lambda: _bool("NR_VIDEO_ENABLED", False))
    admin_token: str | None = field(default_factory=lambda: _secret("NR_ADMIN_TOKEN"))

    # Scheduler.
    hourly_minutes: int = int(os.getenv("NR_HOURLY_MINUTES", "60"))
    breaking_minutes: int = int(os.getenv("NR_BREAKING_MINUTES", "5"))
    morning_hour_ist: int = int(os.getenv("NR_MORNING_HOUR_IST", "7"))
    max_story_age_hours: int = int(os.getenv("NR_MAX_STORY_AGE_HOURS", "36"))
    breaking_age_minutes: int = int(os.getenv("NR_BREAKING_AGE_MINUTES", "25"))

    # Agents. A missing key means publishable generation is held for review.
    # GROK_API_KEY / OPENAI_API_KEY are supported as direct secret-name aliases
    # for GitHub Actions; the OpenAI-compatible base URL follows the key used.
    grok_mode: bool = bool(os.getenv("GROK_API_KEY") or os.getenv("GROK_API_KEY_FILE"))
    llm_api_base: str = (os.getenv("NR_LLM_API_BASE")
                         or ("https://api.x.ai/v1" if os.getenv("GROK_API_KEY") or os.getenv("GROK_API_KEY_FILE")
                             else "https://api.openai.com/v1")).rstrip("/")
    llm_model: str | None = (os.getenv("NR_LLM_MODEL")
                             or ("grok-4.7" if os.getenv("GROK_API_KEY") or os.getenv("GROK_API_KEY_FILE") else None))
    translator_model: str | None = (os.getenv("NR_ROMAN_URDU_MODEL") or os.getenv("NR_LLM_MODEL")
                                    or ("grok-4.7" if os.getenv("GROK_API_KEY") or os.getenv("GROK_API_KEY_FILE") else None))
    validator_model: str | None = (os.getenv("NR_VALIDATOR_MODEL") or os.getenv("NR_LLM_MODEL")
                                   or ("grok-4.6" if os.getenv("GROK_API_KEY") or os.getenv("GROK_API_KEY_FILE") else None))
    llm_api_key: str | None = field(default_factory=lambda: _first_secret(
        "NR_LLM_API_KEY", "GROK_API_KEY", "OPENAI_API_KEY"))

    # Facebook secrets are loaded into memory only, never persisted.
    facebook_user_token: str | None = field(default_factory=lambda: _first_secret(
        "NR_FB_USER_ACCESS_TOKEN", "FB_USER_ACCESS_TOKEN"))
    facebook_page_token: str | None = field(default_factory=lambda: _first_secret(
        "NR_FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ACCESS_TOKEN"))

    # Pixabay Content License photos. The key is read from a secret or *_FILE
    # path; downloaded files and sanitized API responses are cached locally.
    pixabay_api_key: str | None = field(default_factory=lambda: _first_secret(
        "NR_PIXABAY_API_KEY", "PIXABAY_API_KEY"))
    pixabay_api_base: str = field(default_factory=lambda: (
        os.getenv("NR_PIXABAY_API_BASE", "https://pixabay.com/api/").rstrip("/") + "/"))
    pixabay_enabled: bool = field(default_factory=lambda: _bool("NR_PIXABAY_ENABLED", True))
    instagram_enabled: bool = field(default_factory=lambda: _bool("NR_INSTAGRAM_ENABLED", True))
    instagram_user_id: str | None = field(
        default_factory=lambda: os.getenv("NR_INSTAGRAM_USER_ID") or None)
    pixabay_per_page: int = int(os.getenv("NR_PIXABAY_PER_PAGE", "3"))
    pixabay_min_width: int = int(os.getenv("NR_PIXABAY_MIN_WIDTH", "800"))
    pixabay_min_height: int = int(os.getenv("NR_PIXABAY_MIN_HEIGHT", "600"))
    pixabay_cache_ttl: int = int(os.getenv("NR_PIXABAY_CACHE_TTL", "86400"))
    pixabay_license_ref: str = "https://pixabay.com/service/terms/"
    pixabay_source_name: str = "Pixabay"
    max_photos_per_card: int = int(os.getenv("NR_MAX_PHOTOS_PER_CARD", "2"))

    # Source endpoints. These are public/permitted feeds or public pages.
    # Etemaad's /robots.txt is an HTML page containing <meta name=robots
    # content="index,follow">; that host is explicitly allowed to use the
    # equivalent machine-readable policy after verifying the meta tag.
    html_robots_allow_hosts: tuple[str, ...] = ("www.etemaaddaily.com", "etemaaddaily.com")
    munsif_rss: str = "https://munsifdaily.com/feed/"
    etemaad_urdu_home: str = "https://www.etemaaddaily.com/index.php"
    indian_express_rss: str = "https://indianexpress.com/feed/"
    wire_api: str = "https://cms.thewire.in/wp-json/wp/v2/posts"
    user_agent: str = "NRNewsBot/1.0 (+dry-run newsroom; contact: operator)"
    request_timeout: int = int(os.getenv("NR_HTTP_TIMEOUT", "20"))
    min_request_interval: float = float(os.getenv("NR_MIN_REQUEST_INTERVAL", "2.0"))

    # Official corroboration allowlist (host suffix matching).
    official_host_suffixes: tuple[str, ...] = (
        "gov.in", "nic.in", "tgpsc.gov.in", "telangana.gov.in",
        "supremecourtofindia.nic.in", "eci.gov.in", "pib.gov.in",
    )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.card_dir, self.audit_dir, self.media_dir, self.snapshot_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def publish_safety_ok(self) -> bool:
        return (
            self.publish_safety_switch
            and not self.dry_run
            and self.publish_enabled
            and self.operator_approved
            and self.verify_approved
            and self.auto_post_enabled
            and bool(self.facebook_page_token)
            and bool(self.expected_page_name)
        )

    @property
    def secret_values(self) -> tuple[str, ...]:
        values = [
            self.llm_api_key, self.facebook_user_token,
            self.facebook_page_token, self.admin_token,
            self.pixabay_api_key,
        ]
        return tuple(v for v in values if v)


CONFIG = Config()
CONFIG.ensure_dirs()
