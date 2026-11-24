"""
TC Platform — WSGI entry point for Render / gunicorn.

    gunicorn wsgi:app --bind 0.0.0.0:$PORT

(run.py = local dev server, serve.py = local waitress; both still work.)
"""
from app import create_app

app = create_app()
app.config["PUSH_DISPATCHER"] = True   # Web Push dispatcher (app/services/push.py)
