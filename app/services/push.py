"""
TC Platform — Web Push (installed-app notifications while the app is closed).

Standard Web Push + VAPID through pywebpush; no third-party account. The bell
stays the source of truth: a background dispatcher reads NEW rows from the
`notifications` table and pushes the ones ADDRESSED to a person (target_user
set) to that person's registered devices. Broadcast rows (target_user NULL:
integration/health alerts pulled from the four systems, seed rows) are never
pushed — they would go to everybody.

Duplicate safety across gunicorn workers/instances: every cycle CLAIMS its rows
with one `UPDATE ... SET push_claim=<unique token> WHERE push_claim IS NULL`,
then pushes only the rows carrying its own token. A row can be claimed once.

Everything here is best-effort: a failure is logged (never the private key) and
can never break a request or the bell.
"""
import base64
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from config import Config
from app.db import get_db, utcnow

log = logging.getLogger("tc.push")

INTERVAL = 10            # seconds between dispatcher cycles
BATCH = 200              # notifications examined per cycle
PER_USER = 5             # pushes per device per cycle; more -> one summary push
STALE = timedelta(hours=1)   # a row older than this is never pushed (dispatcher was off)
MAX_DEVICES = 10         # per user; the oldest registrations are dropped beyond this
TIMEOUT = 10             # seconds per push-service request
DEFAULT_URL = "/my-work"

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    user_agent  TEXT,
    created_at  TEXT,
    last_ok_at  TEXT,
    fail_count  INTEGER DEFAULT 0,
    since_id    INTEGER DEFAULT 0      -- never push notifications at or below this id
);
CREATE INDEX IF NOT EXISTS ix_push_sub_user ON push_subscriptions(username);
-- small key/value store: VAPID keys (when not in env) and the dispatcher floor
CREATE TABLE IF NOT EXISTS push_kv (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    k  TEXT NOT NULL UNIQUE,
    v  TEXT
);
"""

_TEXT = {
    "summary": {"en": "%d new notifications", "ar": "لديك %d إشعارات جديدة",
                "tr": "%d yeni bildirim"},
    "test": {"en": "Test notification — this device will receive your notifications.",
             "ar": "إشعار تجريبي — سيستقبل هذا الجهاز إشعاراتك.",
             "tr": "Test bildirimi — bu cihaz bildirimlerinizi alacak."},
}
APP_TITLE = "TC Platform"


def ensure_schema(conn):
    """Additive + idempotent (SQLite and PostgreSQL). Called from init_db."""
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        conn.execute("ALTER TABLE notifications ADD COLUMN push_claim TEXT")
        conn.commit()
    except Exception:
        conn.rollback()   # already there
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS ix_notifications_push "
                     "ON notifications(push_claim, id)")
        conn.commit()
    except Exception:
        conn.rollback()


def available():
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# key/value + VAPID
# --------------------------------------------------------------------------
def _kv_get_or_init(conn, key, make):
    """Insert-if-absent then re-read: every worker ends up with the SAME value."""
    row = conn.execute("SELECT v FROM push_kv WHERE k=?", (key,)).fetchone()
    if row:
        return row["v"]
    conn.execute("INSERT OR IGNORE INTO push_kv (k, v) VALUES (?, ?)", (key, make()))
    conn.commit()
    return conn.execute("SELECT v FROM push_kv WHERE k=?", (key,)).fetchone()["v"]


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _new_vapid():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    return json.dumps({
        "private": _b64(key.private_numbers().private_value.to_bytes(32, "big")),
        "public": _b64(key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
    })


_VAPID = {}


def vapid_keys(conn=None):
    """{'public', 'private'}: env TC_VAPID_PUBLIC_KEY/TC_VAPID_PRIVATE_KEY when both
    are set, else generated once and kept in push_kv. Never log the private key."""
    pub = (os.getenv("TC_VAPID_PUBLIC_KEY") or "").strip()
    priv = (os.getenv("TC_VAPID_PRIVATE_KEY") or "").strip()
    if pub and priv:
        return {"public": pub, "private": priv}
    if _VAPID:
        return dict(_VAPID)
    own = conn is None
    conn = conn or get_db()
    try:
        keys = json.loads(_kv_get_or_init(conn, "vapid", _new_vapid))
    finally:
        if own:
            conn.close()
    _VAPID.update(keys)
    return dict(_VAPID)


_ORIGIN = {"url": ""}


def note_origin(url_root):
    if not _ORIGIN["url"] and url_root:
        _ORIGIN["url"] = url_root


def vapid_sub():
    """VAPID 'sub' claim: https://<host> of TC_PUBLIC_URL, else of the host the app
    is served on. Never an email address. py-vapid accepts https://host only."""
    host = urlsplit(Config.PUBLIC_URL or _ORIGIN["url"] or "").hostname or "localhost"
    return "https://" + host


# --------------------------------------------------------------------------
# subscriptions
# --------------------------------------------------------------------------
# Browser push services. A device endpoint must be on one of these: the server
# POSTs to it, so an arbitrary https URL would let any user aim it at other hosts.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com",   # Chrome, Edge-Android, Samsung, Opera
              "push.services.mozilla.com",                      # Firefox
              "notify.windows.com",                             # Edge on Windows (WNS)
              "push.apple.com")                                 # Safari / iOS home-screen apps


def _push_host(host):
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


_B64CH = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")


def validate_subscription(data):
    """Return (endpoint, p256dh, auth) or None."""
    if not isinstance(data, dict):
        return None
    endpoint = data.get("endpoint")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(endpoint, str) and isinstance(p256dh, str) and isinstance(auth, str)):
        return None
    if len(endpoint) > 1024 or not endpoint.startswith("https://"):
        return None
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return None
    if not _push_host(parts.hostname) or any(c.isspace() for c in endpoint):
        return None
    if not (40 <= len(p256dh) <= 200 and 8 <= len(auth) <= 64):
        return None
    if not (set(p256dh) <= _B64CH and set(auth) <= _B64CH):
        return None
    return endpoint, p256dh, auth


def _max_notification_id(conn):
    row = conn.execute("SELECT MAX(id) AS m FROM notifications").fetchone()
    return int(row["m"] or 0)


def subscribe(conn, username, endpoint, p256dh, auth, user_agent=""):
    """Upsert by endpoint and bind it to `username` (a device re-registered by
    another account moves to that account). Only notifications created from now
    on are pushed to it."""
    since = _max_notification_id(conn)
    now = utcnow()
    ua = (user_agent or "")[:300]
    conn.execute("INSERT OR IGNORE INTO push_subscriptions "
                 "(username, endpoint, p256dh, auth, user_agent, created_at, fail_count, since_id) "
                 "VALUES (?,?,?,?,?,?,0,?)", (username, endpoint, p256dh, auth, ua, now, since))
    conn.execute("UPDATE push_subscriptions SET username=?, p256dh=?, auth=?, user_agent=?, "
                 "fail_count=0, since_id=? WHERE endpoint=?",
                 (username, p256dh, auth, ua, since, endpoint))
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM push_subscriptions WHERE username=? ORDER BY id DESC", (username,)).fetchall()]
    for old in ids[MAX_DEVICES:]:
        conn.execute("DELETE FROM push_subscriptions WHERE id=?", (old,))
    conn.commit()


def unsubscribe(conn, username, endpoint):
    cur = conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND username=?",
                       (endpoint, username))
    conn.commit()
    return (cur.rowcount or 0) > 0


def user_subscriptions(conn, username):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM push_subscriptions WHERE username=?", (username,)).fetchall()]


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------
def safe_url(link):
    """Same-origin path only (the bell's own link), else the My Work page."""
    if not isinstance(link, str):
        return DEFAULT_URL
    link = link.strip()
    if (not link.startswith("/") or link.startswith("//") or "\\" in link
            or len(link) > 500 or any(ord(c) < 32 for c in link)):
        return DEFAULT_URL
    return link


def _trim(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def payload_for(row):
    return {"title": _trim(row.get("title"), 100) or APP_TITLE,
            "body": _trim(row.get("message"), 180),
            "url": safe_url(row.get("link")),
            "tag": "tc-n-%s" % row.get("id")}


def _lang(code):
    return code if code in ("en", "ar", "tr") else "en"


def deliver(sub, payload, keys):
    """One push. Returns 'ok', 'gone' (404/410: drop the subscription) or 'fail'."""
    try:
        import pywebpush
        pywebpush.webpush(
            subscription_info={"endpoint": sub["endpoint"],
                               "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=keys["private"],
            vapid_claims={"sub": vapid_sub()},   # fresh dict: pywebpush mutates it
            timeout=TIMEOUT, ttl=86400)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        log.warning("web push failed (%s)", status or type(exc).__name__)
        return "fail"


def _record(conn, sub_id, result):
    if result == "ok":
        conn.execute("UPDATE push_subscriptions SET last_ok_at=?, fail_count=0 WHERE id=?",
                     (utcnow(), sub_id))
    elif result == "gone":
        conn.execute("DELETE FROM push_subscriptions WHERE id=?", (sub_id,))
    else:
        conn.execute("UPDATE push_subscriptions SET fail_count=COALESCE(fail_count,0)+1 "
                     "WHERE id=?", (sub_id,))


def send_test(username, lang="en"):
    """Push a test to the user's OWN devices, off the request thread."""
    conn = get_db()
    try:
        subs = user_subscriptions(conn, username)
        keys = vapid_keys(conn) if subs else None
    finally:
        conn.close()
    if not subs:
        return 0
    payload = {"title": APP_TITLE, "body": _TEXT["test"][_lang(lang)],
               "url": DEFAULT_URL, "tag": "tc-test"}

    def run():
        results = [(s["id"], deliver(s, payload, keys)) for s in subs]
        c = get_db()
        try:
            for sid, res in results:
                _record(c, sid, res)
            c.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("web push test bookkeeping failed: %s", type(exc).__name__)
        finally:
            c.close()

    threading.Thread(target=run, name="tc-push-test", daemon=True).start()
    return len(subs)


# --------------------------------------------------------------------------
# dispatcher
# --------------------------------------------------------------------------
_FLOOR = {}


def _fresh(created_at, now):
    try:
        ts = datetime.strptime(str(created_at)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return True
    return now - ts.replace(tzinfo=timezone.utc) <= STALE


def dispatch_once():
    """One cycle. Returns the number of pushes attempted. Safe to run concurrently
    from several workers: each row is claimed by exactly one of them."""
    if not available():
        return 0
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM push_subscriptions LIMIT 1").fetchone():
            return 0
        if "v" not in _FLOOR:
            # first run: everything already in the table is history and never pushed
            _FLOOR["v"] = int(_kv_get_or_init(conn, "floor",
                                              lambda: str(_max_notification_id(conn))))
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notifications WHERE push_claim IS NULL AND id > ? "
            "ORDER BY id LIMIT %d" % BATCH, (_FLOOR["v"],)).fetchall()]
        if not ids:
            return 0
        token = "%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                           secrets.token_hex(6))
        conn.execute("UPDATE notifications SET push_claim=? WHERE push_claim IS NULL "
                     "AND id IN (%s)" % ",".join("?" * len(ids)), (token, *ids))
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, target_user, module, title, message, link, created_at "
            "FROM notifications WHERE push_claim=? ORDER BY id", (token,)).fetchall()]

        now = datetime.now(timezone.utc)
        by_user = {}
        for r in rows:
            # addressed to a person + still fresh; broadcast rows are never pushed
            if r["target_user"] and _fresh(r["created_at"], now):
                by_user.setdefault(r["target_user"], []).append(r)
        if not by_user:
            return 0
        names = list(by_user)
        ph = ",".join("?" * len(names))
        users = {u["username"]: u for u in conn.execute(
            "SELECT username, role, lang_pref FROM users WHERE is_active=1 "
            "AND username IN (%s)" % ph, tuple(names)).fetchall()}
        subs = conn.execute("SELECT * FROM push_subscriptions WHERE username IN (%s)" % ph,
                            tuple(names)).fetchall()
        if not subs:
            return 0
        keys = vapid_keys(conn)
        from app.security import system_scope
        sent, results = 0, []
        for sub in subs:
            user = users.get(sub["username"])
            if not user:
                continue
            scope = system_scope({"role": user["role"]})
            # same rule the bell applies: scope-locked roles only see their systems
            mine = [r for r in by_user[sub["username"]] if r["id"] > (sub["since_id"] or 0)
                    and (scope is None or not r["module"] or r["module"] in scope)]
            if not mine:
                continue
            if len(mine) > PER_USER:
                payloads = [{"title": APP_TITLE,
                             "body": _TEXT["summary"][_lang(user["lang_pref"])] % len(mine),
                             "url": DEFAULT_URL, "tag": "tc-summary"}]
            else:
                payloads = [payload_for(r) for r in mine]
            for p in payloads:
                res = deliver(sub, p, keys)
                sent += 1
                results.append((sub["id"], res))
                if res == "gone":
                    break
        for sid, res in results:
            _record(conn, sid, res)
        conn.commit()
        return sent
    finally:
        conn.close()


_STATE = {"pid": None, "last_err": None}
_LOCK = threading.Lock()


def _loop(interval):
    while True:
        time.sleep(interval)
        try:
            dispatch_once()
            _STATE["last_err"] = None
        except Exception as exc:  # noqa: BLE001
            msg = type(exc).__name__
            if msg != _STATE["last_err"]:   # once per distinct error, not every 10 s
                log.warning("web push dispatcher cycle failed: %s", msg)
            _STATE["last_err"] = msg


def start_dispatcher(interval=INTERVAL):
    """Start the per-process dispatcher thread once (after a fork too: keyed by pid).
    TC_PUSH_DISABLED=1 turns it off."""
    if (os.getenv("TC_PUSH_DISABLED") or "").strip() == "1":
        return False
    with _LOCK:
        if _STATE["pid"] == os.getpid():
            return False
        _STATE["pid"] = os.getpid()
    threading.Thread(target=_loop, args=(interval,), name="tc-push", daemon=True).start()
    return True
