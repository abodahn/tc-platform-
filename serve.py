"""
TC Platform — production server entry (waitress, a real WSGI server).

    pip install waitress
    set TC_ENV=production
    python serve.py

Unlike run.py (Flask's dev server), this is suitable for Windows Server.
Put it behind IIS / nginx for TLS in production.
"""
from config import Config
from app import create_app

app = create_app()

if __name__ == "__main__":
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit("waitress is not installed. Run:  pip install waitress")

    print("=" * 60)
    print("  TC Platform (production / waitress)")
    print(f"  Serving on http://{Config.HOST}:{Config.PORT}  (env={Config.ENV})")
    print("=" * 60)
    serve(app, host=Config.HOST, port=Config.PORT, threads=8)
