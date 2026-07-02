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
    """Resolve a STABLE secret key so login sessions and CSRF tokens survive
    across gunicorn workers and restarts. A changing key silently invalidates
    every open session, which shows up to users as "Invalid or missing CSRF
    token" on the next form they submit. Resolution order:

      1. TC_SECRET_KEY from the environment — best: survives even redeploys.
         Set this on Render (Settings -> Environment) to a long random value.
      2. A generated key persisted to <data dir>/.secret_key, shared by every
         worker and stable across restarts within the same container. (On a
         free-tier ephemeral disk this is wiped on each *redeploy*, logging
         users out once — set TC_SECRET_KEY to avoid even that.)
      3. An ephemeral per-process key — only if the filesystem is read-only.
    """
    env = (os.getenv("TC_SECRET_KEY") or "").strip()
    if env and env not in _INSECURE_DEFAULTS:
        return env

    keyfile = Path(os.getenv("TC_DATA_DIR") or str(BASE_DIR)) / ".secret_key"
    try:
        if keyfile.exists():
            saved = keyfile.read_text(encoding="utf-8").strip()
            if saved:
                return saved
    except Exception:
        pass

    generated = secrets.token_hex(32)
    try:
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: only the first worker creates+writes the key; any other worker
        # racing at startup gets FileExistsError and reads the winner's key, so
        # all workers converge on the same value (no per-worker mismatch).
        try:
            fd = os.open(str(keyfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, generated.encode("utf-8"))
            finally:
                os.close(fd)
            return generated
        except FileExistsError:
            saved = keyfile.read_text(encoding="utf-8").strip()
            return saved or generated
    except Exception:
        # Read-only filesystem: fall back to a per-process key (sessions will
        # reset on restart). Set TC_SECRET_KEY to make them stable.
        if os.getenv("TC_ENV", "development").lower() == "production":
            print("WARNING: TC_SECRET_KEY unset and key file unwritable — "
                  "sessions will reset on restart. Set TC_SECRET_KEY on Render.")
        return generated


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

    # --- Observability & external alerting -------------------------------
    # Email (SMTP) for critical alerts + 500 error reports. Leave TC_SMTP_HOST
    # blank to disable email — the app still works, alerts just log locally.
    SMTP_HOST = (os.getenv("TC_SMTP_HOST", "") or "").strip()
    SMTP_PORT = int(os.getenv("TC_SMTP_PORT", "587"))
    SMTP_USER = (os.getenv("TC_SMTP_USER", "") or "").strip()
    SMTP_PASS = os.getenv("TC_SMTP_PASS", "") or ""
    SMTP_FROM = (os.getenv("TC_SMTP_FROM", "") or os.getenv("TC_SMTP_USER", "")
                 or "tc-platform@tcgarments.com").strip()
    SMTP_TLS = _bool(os.getenv("TC_SMTP_TLS"), True)
    # Comma-separated recipients for alerts & error reports.
    ALERT_EMAILS = [e.strip() for e in (os.getenv("TC_ALERT_EMAILS", "") or "").split(",") if e.strip()]
    # Generic webhook (WhatsApp / Slack / Teams / n8n) — gets a JSON POST per alert.
    ALERT_WEBHOOK_URL = (os.getenv("TC_ALERT_WEBHOOK_URL", "") or "").strip()
    # Only alert at/above this severity (info < warning < critical).
    ALERT_MIN_SEVERITY = (os.getenv("TC_ALERT_MIN_SEVERITY", "critical") or "critical").strip().lower()
    # Sentry error tracking (optional). Set TC_SENTRY_DSN to enable.
    SENTRY_DSN = (os.getenv("TC_SENTRY_DSN", "") or "").strip()
    # Public URL of the platform (used in alert emails so links are clickable).
    PUBLIC_BASE_URL = (os.getenv("TC_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    # --- Auto-ticketing: turn alerts into Service Desk tickets ------------
    # ON by default (enabled for T&C). Critical alerts from the source systems
    # below are auto-opened as ITSM tickets (deduped, one ticket per alert).
    # To disable: set TC_AUTO_TICKET_ENABLED=false on Render and redeploy.
    AUTO_TICKET_ENABLED = _bool(os.getenv("TC_AUTO_TICKET_ENABLED"), True)
    AUTO_TICKET_TARGET = (os.getenv("TC_AUTO_TICKET_TARGET", "itsm") or "itsm").strip()
    # only alerts FROM these source systems are ticketed (avoids ticketing ITSM's
    # own alerts back into ITSM). Comma-separated system keys.
    AUTO_TICKET_SOURCE_MODULES = (os.getenv("TC_AUTO_TICKET_SOURCE_MODULES", "monitoring") or "").strip()
    AUTO_TICKET_MIN_SEVERITY = (os.getenv("TC_AUTO_TICKET_MIN_SEVERITY", "critical") or "critical").strip().lower()
    AUTO_TICKET_REQUESTER = (os.getenv("TC_AUTO_TICKET_REQUESTER", "platform-automation") or "").strip()

    # --- Single Sign-On (SSO): platform is the Identity Provider -----------
    # One platform login → the platform mints a short-lived signed token when
    # a user opens an integrated system, and that system trusts it and logs the
    # user in locally. The SAME secret must be set on the platform AND on all
    # four systems (TC_SSO_SECRET). Fails closed: if the secret is missing or
    # weak, SSO is disabled and modules open with their normal manual login.
    SSO_SECRET = (os.getenv("TC_SSO_SECRET", "") or "").strip()
    SSO_TOKEN_TTL = int(os.getenv("TC_SSO_TTL", "120"))  # seconds; short by design
    SSO_SP_PATH = (os.getenv("TC_SSO_SP_PATH", "/sso/login") or "/sso/login").strip()
    # Enabled only when explicitly on AND the shared secret is strong enough.
    SSO_ENABLED = _bool(os.getenv("TC_SSO_ENABLED"), True) and (
        len(SSO_SECRET) >= 16
        and SSO_SECRET.lower() not in {
            "", "change-me", "changeme", "secret",
            "tc-sso-dev-secret-change-me",
            "change-this-to-a-long-random-string-in-production",
        }
    )

    # Writable data dir — local: project folder; Render: TC_DATA_DIR (a disk on
    # paid plans, or an ephemeral path like /tmp on free tier).
    DATA_DIR = Path(os.getenv("TC_DATA_DIR", str(BASE_DIR)))

    # Backups created by the platform itself live here
    BACKUP_DIR = DATA_DIR / "backups"

    # Uploads (maintenance photo proof, etc.) — served auth-gated, never public
    UPLOAD_DIR = DATA_DIR / "uploads"
    MAX_CONTENT_LENGTH = int(os.getenv("TC_MAX_UPLOAD_MB", "12")) * 1024 * 1024
    ALLOWED_UPLOAD_EXT = {"jpg", "jpeg", "png", "webp", "gif", "mp4", "pdf",
                          "webm", "m4a", "ogg", "mp3", "wav"}  # audio = voice notes
