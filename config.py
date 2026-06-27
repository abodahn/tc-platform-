"""
TC Platform — central configuration.

Reads from environment variables (.env). Keeps secrets out of source code.
Development uses SQLite (platform.db). Production-ready for PostgreSQL via
TC_DATABASE_URL (a future enhancement — current persistence layer is SQLite).
"""
import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv optional; env vars still work without it
    pass

BASE_DIR = Path(__file__).resolve().parent

_INSECURE_DEFAULTS = {
    "", "change-me", "tc-platform-dev-secret-change-me",
    "change-this-to-a-long-random-string-in-production",
}


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_secret_key() -> str:
    """Use TC_SECRET_KEY if set to a real value; otherwise (LOCAL DEV ONLY)
    generate a strong key once and persist it to .secret_key. On Render the
    filesystem is ephemeral, so TC_SECRET_KEY MUST come from the environment
    (render.yaml generates it); we never write .secret_key in production."""
    env = (os.getenv("TC_SECRET_KEY") or "").strip()
    if env and env not in _INSECURE_DEFAULTS:
        return env
    if (os.getenv("TC_ENV", "development").lower() == "production"):
        # production with no real key set: use a per-process random key (and warn).
        # Set TC_SECRET_KEY in the environment to keep sessions stable.
        print("WARNING: TC_SECRET_KEY not set in production — using an ephemeral key.")
        return secrets.token_hex(32)
    keyfile = BASE_DIR / ".secret_key"
    try:
        if keyfile.exists():
            saved = keyfile.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        generated = secrets.token_hex(32)
        keyfile.write_text(generated, encoding="utf-8")
        return generated
    except Exception:
        # Last resort (read-only FS): random per-process key
        return secrets.token_hex(32)


class Config:
    ENV = os.getenv("TC_ENV", "development")
    SECRET_KEY = _resolve_secret_key()
    IS_PRODUCTION = ENV.lower() == "production"
    HOST = os.getenv("TC_HOST", "0.0.0.0")
    # Render injects PORT; fall back to TC_PORT then a sane default.
    PORT = int(os.getenv("PORT", os.getenv("TC_PORT", "7000")))
    DEBUG = _bool(os.getenv("TC_DEBUG"), True)

    # SQLite metadata database (local). On Render, DATABASE_URL points at Postgres.
    DB_PATH = BASE_DIR / "platform.db"
    # Render injects DATABASE_URL; TC_DATABASE_URL kept for backward-compat.
    DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("TC_DATABASE_URL") or "").strip()

    SESSION_MINUTES = int(os.getenv("TC_SESSION_MINUTES", "120"))

    ADMIN_USER = os.getenv("TC_ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.getenv("TC_ADMIN_PASSWORD", "Admin@12345")

    INTEGRATION_HOST = os.getenv("TC_INTEGRATION_HOST", "127.0.0.1")
    INTEGRATION_SCHEME = os.getenv("TC_INTEGRATION_SCHEME", "http")  # http | https
    HEALTH_TIMEOUT = float(os.getenv("TC_HEALTH_TIMEOUT", "3"))

    # Per-system PUBLIC base URLs (optional). When set, they re-point the four
    # integrated systems so a cloud deploy can reach them over the internet.
    # Give the base URL only (e.g. https://itsm.example.com or http://1.2.3.4:5000);
    # the correct health path is appended automatically. Leave blank to keep the
    # value already stored (seeded from TC_INTEGRATION_HOST). Editable later in
    # Admin -> Integrations too.
    SYSTEM_URLS = {
        "itsm": (os.getenv("TC_URL_ITSM", "") or "").strip().rstrip("/"),
        "assets": (os.getenv("TC_URL_ASSETS", "") or "").strip().rstrip("/"),
        "monitoring": (os.getenv("TC_URL_MONITORING", "") or "").strip().rstrip("/"),
        "commandtrack": (os.getenv("TC_URL_COMMANDTRACK", "") or "").strip().rstrip("/"),
    }

    # Writable data dir — local: project folder; Render: TC_DATA_DIR (a disk on
    # paid plans, or an ephemeral path like /tmp on free tier).
    DATA_DIR = Path(os.getenv("TC_DATA_DIR", str(BASE_DIR)))

    # Backups created by the platform itself live here
    BACKUP_DIR = DATA_DIR / "backups"

    # Uploads (maintenance photo proof, etc.) — served auth-gated, never public
    UPLOAD_DIR = DATA_DIR / "uploads"
    MAX_CONTENT_LENGTH = int(os.getenv("TC_MAX_UPLOAD_MB", "12")) * 1024 * 1024
    ALLOWED_UPLOAD_EXT = {"jpg", "jpeg", "png", "webp", "gif", "mp4", "pdf"}
