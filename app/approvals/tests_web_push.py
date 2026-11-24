"""Web Push: device registration, VAPID keys and the bell -> push dispatcher.

    python -m app.approvals.tests_web_push

Lives next to the procurement suites so the fast runner (approvals/maintenance/
warehouse tests_*.py) picks it up; the feature itself is app/services/push.py.
The network send (pywebpush.webpush) is mocked: nothing leaves this machine.

Proves: routes need a login + CSRF and validate input; an endpoint re-registered
by another account moves to it; VAPID keys are generated once and reused (env
wins); a notification addressed to A reaches only A's devices; a role-targeted
notification reaches the role holders; broadcast rows are never pushed; no
history on first run; two dispatch runs push once; 404/410 drop the device;
>5 new rows -> one summary; the payload url is same-origin only; the app and the
bell keep working when pywebpush is missing or the dispatcher raises.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


SENT = []          # (endpoint, payload dict, claims)
SENT_LOCK = threading.Lock()


def fake_webpush(subscription_info, data=None, vapid_private_key=None, vapid_claims=None, **kw):
    import pywebpush
    ep = subscription_info["endpoint"]
    with SENT_LOCK:
        SENT.append((ep, json.loads(data), dict(vapid_claims or {}), kw))
    for marker, code in (("gone", 410), ("nf", 404), ("boom", 500)):
        if marker in ep:
            resp = type("R", (), {"status_code": code, "text": "", "reason": "x", "headers": {}})()
            raise pywebpush.WebPushException("push failed", response=resp)
    return type("R", (), {"status_code": 201})()


def sent_to(ep):
    return [p for e, p, _c, _k in SENT if e == ep]


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("TC_VAPID_PUBLIC_KEY", None)
    os.environ.pop("TC_VAPID_PRIVATE_KEY", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="webpush_"), "p.db")
    import pywebpush
    pywebpush.webpush = fake_webpush
    from app import create_app
    from app.db import get_db, utcnow
    from app.services import push
    push.PUSH_HOSTS += ("push.example.test",)   # the fake push service used below

    app = create_app()
    app.testing = True       # the dispatcher must not start by itself under tests

    conn = get_db()
    for name, role in (("alice", "normal_user"), ("bob", "normal_user"),
                       ("mm1", "maintenance_manager"), ("mm2", "maintenance_manager"),
                       ("itsmu", "itsm_user"), ("gone_user", "normal_user")):
        conn.execute("INSERT INTO users (username, password_hash, full_name, role, lang_pref, "
                     "created_at) VALUES (?,?,?,?,?,?)", (name, "x", name, role, "en", utcnow()))
    conn.commit()
    ids = {r["username"]: r["id"] for r in conn.execute("SELECT id, username FROM users")}
    conn.close()

    def client_for(username):
        c = app.test_client()
        with c.session_transaction() as s:
            if username:
                s["uid"] = ids[username]
                s["ep"] = 0
            s["_csrf_token"] = "tok"
        return c

    H = {"X-CSRF-Token": "tok"}

    def sub_body(ep):
        return {"endpoint": ep, "keys": {"p256dh": "B" * 87, "auth": "A" * 22}}

    def sub_row(ep):
        c = get_db()
        try:
            r = c.execute("SELECT * FROM push_subscriptions WHERE endpoint=?", (ep,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()

    def notify(user, title, msg="", link=None, module="procurement", created=None):
        c = get_db()
        try:
            cur = c.execute("INSERT INTO notifications (severity, module, title, message, "
                            "target_user, link, created_at) VALUES (?,?,?,?,?,?,?)",
                            ("info", module, title, msg, user, link, created or utcnow()))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()

    print("\n--- 1. routes: auth, CSRF, validation, ownership -------------------")
    anon = client_for(None)
    chk("GET /push/key needs a login", anon.get("/push/key").status_code == 302)
    chk("POST /push/subscribe needs a login",
        anon.post("/push/subscribe", json=sub_body("https://push.example.test/x"),
                  headers=H).status_code == 302)
    a, b = client_for("alice"), client_for("bob")
    chk("subscribe without the CSRF header is refused (400)",
        a.post("/push/subscribe", json=sub_body("https://push.example.test/x")).status_code == 400)
    for label, body in (("http endpoint", sub_body("http://push.example.test/x")),
                        ("no keys", {"endpoint": "https://push.example.test/x"}),
                        ("oversized endpoint", sub_body("https://push.example.test/" + "x" * 2000)),
                        ("bad key characters", {"endpoint": "https://push.example.test/x",
                                                "keys": {"p256dh": "<script>" * 10, "auth": "A" * 22}}),
                        ("not an object", ["https://push.example.test/x"]),
                        ("a host that is not a push service", sub_body("https://intranet.example/x")),
                        ("a look-alike host", sub_body("https://fcm.googleapis.com.evil.example/x"))):
        chk("rejects " + label, a.post("/push/subscribe", json=body, headers=H).status_code == 400)
    chk("nothing was stored by the rejected calls", sub_row("https://push.example.test/x") is None)
    chk("real push services are accepted",
        all(push.validate_subscription(sub_body(u)) for u in (
            "https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x",
            "https://wns2-par02p.notify.windows.com/w/?token=x", "https://web.push.apple.com/QF")))

    r = a.get("/push/key")
    key = (r.get_json() or {}).get("key")
    chk("GET /push/key returns the public VAPID key", r.status_code == 200 and key
        and key == push.vapid_keys()["public"])
    chk("the private key is never served", push.vapid_keys()["private"] not in r.get_data(as_text=True))

    E1 = "https://push.example.test/shared-device"
    chk("alice registers a device", a.post("/push/subscribe", json=sub_body(E1), headers=H).status_code == 200
        and sub_row(E1)["username"] == "alice")
    chk("bob re-registers the same endpoint -> it moves to bob",
        b.post("/push/subscribe", json=sub_body(E1), headers=H).status_code == 200
        and sub_row(E1)["username"] == "bob")
    c = get_db()
    chk("still one row for that endpoint",
        c.execute("SELECT COUNT(*) n FROM push_subscriptions WHERE endpoint=?", (E1,)).fetchone()["n"] == 1)
    c.close()
    r = a.post("/push/unsubscribe", json={"endpoint": E1}, headers=H)
    chk("alice cannot remove bob's device", r.status_code == 200 and r.get_json()["removed"] is False
        and sub_row(E1)["username"] == "bob")
    chk("bob removes his own device",
        b.post("/push/unsubscribe", json={"endpoint": E1}, headers=H).get_json()["removed"] is True
        and sub_row(E1) is None)

    EA, EB = "https://push.example.test/alice-phone", "https://push.example.test/bob-laptop"
    a.post("/push/subscribe", json=sub_body(EA), headers=H)
    b.post("/push/subscribe", json=sub_body(EB), headers=H)
    chk("test push without CSRF is refused", a.post("/push/test").status_code == 400)
    r = a.post("/push/test", headers=H)
    chk("send-test answers at once with the device count", r.status_code == 200
        and r.get_json()["devices"] == 1, r.get_json())
    for t in [t for t in threading.enumerate() if t.name == "tc-push-test"]:
        t.join(5)
    chk("the test push went to alice's device only",
        len(sent_to(EA)) == 1 and not sent_to(EB) and sent_to(EA)[0]["tag"] == "tc-test", SENT)
    c3 = client_for("mm2")
    chk("send-test with no device -> 404 no_devices",
        c3.post("/push/test", headers=H).status_code == 404)

    print("\n--- 2. VAPID keys ----------------------------------------------------")
    k1 = push.vapid_keys()
    push._VAPID.clear()            # as a second worker would: read from the DB
    k2 = push.vapid_keys()
    c = get_db()
    nkeys = c.execute("SELECT COUNT(*) n FROM push_kv WHERE k='vapid'").fetchone()["n"]
    c.close()
    chk("generated once, the second read returns the same pair", k1 == k2 and nkeys == 1)
    chk("public key is a 65-byte uncompressed P-256 point (87 b64url chars)", len(k1["public"]) == 87)
    os.environ["TC_VAPID_PUBLIC_KEY"], os.environ["TC_VAPID_PRIVATE_KEY"] = "envpub", "envpriv"
    chk("env TC_VAPID_* overrides the stored pair",
        push.vapid_keys() == {"public": "envpub", "private": "envpriv"})
    del os.environ["TC_VAPID_PUBLIC_KEY"], os.environ["TC_VAPID_PRIVATE_KEY"]
    chk("without env, the stored pair is back", push.vapid_keys() == k1)
    from py_vapid import Vapid
    try:
        Vapid.from_string(k1["private"]).sign({"sub": push.vapid_sub(), "aud": "https://push.example.test"})
        signs = True
    except Exception as exc:  # noqa: BLE001
        signs = exc
    chk("the generated private key signs a VAPID claim", signs is True, signs)
    chk("VAPID sub is an https host, never an email",
        push.vapid_sub().startswith("https://") and "@" not in push.vapid_sub(), push.vapid_sub())

    print("\n--- 3. dispatcher ------------------------------------------------------")
    hist = notify("alice", "History", "old row")
    c = get_db()
    c.execute("UPDATE push_subscriptions SET since_id=0")     # an old registration
    c.commit()
    c.close()
    SENT.clear()
    push.dispatch_once()
    chk("first run pushes no history", not SENT and push._FLOOR.get("v", 0) >= hist, SENT)

    na = notify("alice", "PR-7 needs your signature", "Please sign  PR-7\n now",
                link="/procurement/pr/7")
    nb = notify("bob", "Bob only", "for bob")
    notify(None, "Monitoring: host down", "broadcast", module="monitoring")
    SENT.clear()
    push.dispatch_once()
    pa = sent_to(EA)
    chk("A's notification reaches A's device", len(pa) == 1 and pa[0]["title"] == "PR-7 needs your signature", SENT)
    chk("payload: trimmed body, same-origin url, tag",
        pa and pa[0]["body"] == "Please sign PR-7 now" and pa[0]["url"] == "/procurement/pr/7"
        and pa[0]["tag"] == "tc-n-%d" % na, pa)
    chk("B's notification reaches only B", [p["title"] for p in sent_to(EB)] == ["Bob only"])
    chk("the broadcast row is pushed to nobody", all(p["title"] != "Monitoring: host down"
                                                     for _e, p, _c, _k in SENT))
    chk("exactly two pushes", len(SENT) == 2, len(SENT))
    claims = SENT[0][2]
    chk("VAPID claims carry an https sub, sends have a timeout",
        claims.get("sub", "").startswith("https://") and SENT[0][3].get("timeout"), (claims, SENT[0][3]))

    SENT.clear()
    push.dispatch_once()
    chk("running again pushes nothing (already claimed)", not SENT, SENT)

    # two workers at once
    notify("alice", "Race", "once")
    SENT.clear()
    gate = threading.Barrier(2)

    def worker():
        gate.wait()
        try:
            push.dispatch_once()
        except Exception:
            pass
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    chk("two concurrent dispatchers push it exactly once", len(sent_to(EA)) == 1, SENT)

    print("\n--- 4. role-targeted (maintenance notify -> per-user rows) ------------")
    m1 = client_for("mm1")
    EM = "https://push.example.test/mm1-phone"
    m1.post("/push/subscribe", json=sub_body(EM), headers=H)
    from app.maintenance import services as msvc
    c = get_db()
    msvc.notify(c, "maintenance_manager", "Machine down", "Line 3 stopped", "ticket", 1,
                severity="warning", action_url="/maintenance/tickets/1")
    c.commit()
    msvc._flush_notes(c)
    c.close()
    SENT.clear()
    push.dispatch_once()
    chk("the role holder with a device gets it", [p["title"] for p in sent_to(EM)] == ["Machine down"]
        and sent_to(EM)[0]["url"] == "/maintenance/tickets/1", SENT)
    chk("nobody outside the role gets it", not sent_to(EA) and not sent_to(EB), SENT)

    print("\n--- 5. payload url, stale, scope, inactive ----------------------------")
    for bad in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "/\\evil",
                "", None, "/ok\nx"):
        chk("unsafe link %r -> /my-work" % (bad,), push.safe_url(bad) == "/my-work")
    notify("alice", "Evil link", link="https://evil.example/phish")
    notify("alice", "Too old", created="2020-01-01 00:00:00")
    EI = "https://push.example.test/itsm-user"
    client_for("itsmu").post("/push/subscribe", json=sub_body(EI), headers=H)
    notify("itsmu", "Procurement thing", module="procurement")
    notify("itsmu", "Your ticket", module="itsm")
    c = get_db()
    c.execute("UPDATE users SET is_active=0 WHERE username='bob'")
    c.commit()
    c.close()
    notify("bob", "Bob is inactive")
    SENT.clear()
    push.dispatch_once()
    chk("an off-site link is pushed as /my-work",
        [p["url"] for p in sent_to(EA)] == ["/my-work"], sent_to(EA))
    chk("a row older than an hour is not pushed", all(p["title"] != "Too old" for p in sent_to(EA)))
    chk("scope-locked user: only the module the bell shows them",
        [p["title"] for p in sent_to(EI)] == ["Your ticket"], sent_to(EI))
    chk("inactive user gets nothing", not sent_to(EB))
    c = get_db()
    c.execute("UPDATE users SET is_active=1 WHERE username='bob'")
    c.commit()
    c.close()

    print("\n--- 6. 404/410 drop the device, other failures are counted ------------")
    g = client_for("gone_user")
    EG, EN, EX = ("https://push.example.test/gone-1", "https://push.example.test/nf-2",
                  "https://push.example.test/boom-3")
    for ep in (EG, EN, EX):
        g.post("/push/subscribe", json=sub_body(ep), headers=H)
    notify("gone_user", "Hello")
    SENT.clear()
    push.dispatch_once()
    chk("all three devices were tried", len(SENT) == 3, SENT)
    chk("410 -> subscription deleted", sub_row(EG) is None)
    chk("404 -> subscription deleted", sub_row(EN) is None)
    chk("500 -> kept, fail_count 1", (sub_row(EX) or {}).get("fail_count") == 1, sub_row(EX))
    chk("a good device records last_ok_at", bool((sub_row(EA) or {}).get("last_ok_at")))

    print("\n--- 7. more than 5 new rows -> one summary push ------------------------")
    for i in range(7):
        notify("bob", "Bulk %d" % i)
    SENT.clear()
    push.dispatch_once()
    pb = sent_to(EB)
    chk("bob gets ONE summary push", len(pb) == 1 and pb[0]["body"] == "7 new notifications"
        and pb[0]["url"] == "/my-work", pb)
    c = get_db()
    c.execute("UPDATE users SET lang_pref='ar' WHERE username='bob'")
    c.commit()
    c.close()
    for i in range(6):
        notify("bob", "Bulk ar %d" % i)
    SENT.clear()
    push.dispatch_once()
    chk("the summary is in the user's language", sent_to(EB) and "6" in sent_to(EB)[0]["body"]
        and "إشعارات" in sent_to(EB)[0]["body"], sent_to(EB))

    print("\n--- 8. new subscription gets no backlog --------------------------------")
    notify("mm2", "Before mm2 subscribed")
    E2 = "https://push.example.test/mm2"
    client_for("mm2").post("/push/subscribe", json=sub_body(E2), headers=H)
    SENT.clear()
    push.dispatch_once()
    chk("a notification created before the device registered is not pushed", not sent_to(E2), SENT)
    notify("mm2", "After")
    SENT.clear()
    push.dispatch_once()
    chk("...one created after it is", [p["title"] for p in sent_to(E2)] == ["After"], SENT)

    print("\n--- 9. pages + service worker ------------------------------------------")
    r = a.get("/profile")
    html = r.get_data(as_text=True)
    chk("profile renders the device section", r.status_code == 200 and 'id="push"' in html
        and 'data-i18n="push.title"' in html)
    chk("no permission prompt on page load (only inside the Enable click handler)",
        html.count("Notification.requestPermission") == 1
        and html.index("Notification.requestPermission") > html.index('bOn.addEventListener("click"'))
    sw = a.get("/sw.js").get_data(as_text=True)
    chk("sw.js: push + notificationclick + pushsubscriptionchange, cache bumped",
        all(x in sw for x in ('addEventListener("push"', '"notificationclick"',
                              '"pushsubscriptionchange"', '"tcp-v3"')))
    root = Path(__file__).resolve().parents[1] / "static" / "i18n"
    keys = ("push.title", "push.enable", "push.disable", "push.test", "push.status.on",
            "push.status.off", "push.status.blocked", "push.status.unsupported",
            "push.ios_hint", "push.cta")
    for lang in ("en", "ar", "tr"):
        d = json.loads((root / ("%s.json" % lang)).read_text(encoding="utf-8"))
        chk("i18n %s has the push keys" % lang, all(d.get(k) for k in keys))

    print("\n--- 10. disabled under tests / by env; failures stay contained ---------")
    chk("no dispatcher thread was started by requests under app.testing",
        push._STATE["pid"] is None and not any(t.name == "tc-push" for t in threading.enumerate()))
    os.environ["TC_PUSH_DISABLED"] = "1"
    chk("TC_PUSH_DISABLED=1 -> start_dispatcher refuses", push.start_dispatcher() is False)
    del os.environ["TC_PUSH_DISABLED"]

    real = sys.modules.get("pywebpush")
    sys.modules["pywebpush"] = None          # import now raises ImportError
    try:
        notify("alice", "While missing")
        chk("without pywebpush: available() is False", push.available() is False)
        chk("...dispatch does nothing and claims nothing", push.dispatch_once() == 0)
        c = get_db()
        left = c.execute("SELECT COUNT(*) n FROM notifications WHERE title='While missing' "
                         "AND push_claim IS NULL").fetchone()["n"]
        c.close()
        chk("...the row stays unclaimed", left == 1)
        chk("...GET /push/key -> 503", a.get("/push/key").status_code == 503)
        app2 = create_app()
        app2.testing = True
        c2 = app2.test_client()
        with c2.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = ids["alice"], 0, "tok"
        feed = c2.get("/notifications/feed")
        chk("...the app still starts and the bell feed works",
            feed.status_code == 200 and "items" in (feed.get_json() or {}))
        chk("...the profile page still renders", c2.get("/profile").status_code == 200)
    finally:
        sys.modules["pywebpush"] = real

    calls = [0]

    def boom():
        calls[0] += 1
        raise RuntimeError("db down")
    push.dispatch_once = boom                 # stays: the loop thread below keeps calling it
    push._STATE["pid"] = None
    chk("start_dispatcher starts one thread", push.start_dispatcher(interval=0.05) is True)
    chk("...and only once per process", push.start_dispatcher(interval=0.05) is False)
    time.sleep(0.4)
    alive = [t for t in threading.enumerate() if t.name == "tc-push" and t.is_alive()]
    chk("a raising dispatcher keeps looping (thread alive, several cycles)",
        len(alive) == 1 and calls[0] >= 2, calls[0])
    feed = a.get("/notifications/feed")
    chk("...and the bell still answers", feed.status_code == 200)

    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
