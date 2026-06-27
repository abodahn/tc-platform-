"""
TC Platform — entry point.

    python run.py

Reads host/port from .env (defaults: 0.0.0.0:7000). Creates and seeds the
SQLite metadata database on first run. Never modifies existing T&C systems.
"""
from app import create_app
from config import Config

app = create_app()

if __name__ == "__main__":
    print("=" * 60)
    print("  TC Platform — T&C Digital Operations Platform")
    print(f"  Running on http://127.0.0.1:{Config.PORT}  (env={Config.ENV})")
    print(f"  Default login: {Config.ADMIN_USER} / (see .env)")
    print("=" * 60)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
