"""
TC Platform — one-time data migration: SQLite -> Render PostgreSQL.

Copies every row from a local platform.db into the managed PostgreSQL database,
preserving primary-key ids and resetting identity sequences. Idempotent
(ON CONFLICT (id) DO NOTHING); use --fresh to truncate-and-reload.

    pip install psycopg2-binary
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite ./platform.db \
        --database-url "postgresql://USER:PASS@HOST/DBNAME"

Get the DATABASE_URL from Render ▸ your database ▸ Info ▸ External Database URL.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(ROOT / "platform.db"))
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    ap.add_argument("--fresh", action="store_true", help="truncate target tables first")
    args = ap.parse_args()

    if not args.database_url:
        sys.exit("Provide --database-url or set DATABASE_URL")
    if not os.path.exists(args.sqlite):
        sys.exit(f"SQLite file not found: {args.sqlite}")

    import psycopg2
    import psycopg2.extras

    url = args.database_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url.split("://", 1)[1]
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    # 1) ensure the PostgreSQL schema exists (create tables + seed) via the app
    os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("TC_ENV", "production")
    from app.db import init_db
    print("Ensuring PostgreSQL schema (init_db)...")
    init_db()

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row
    pg = psycopg2.connect(url)
    pgc = pg.cursor()

    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    if args.fresh and tables:
        print("--fresh: truncating target tables...")
        pgc.execute("TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
        pg.commit()

    summary = []
    for t in tables:
        cols = [c["name"] for c in src.execute(f"PRAGMA table_info({t})")]
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall()
        src_n = len(rows)
        if rows:
            collist = ", ".join(cols)
            conflict = " ON CONFLICT (id) DO NOTHING" if "id" in cols else " ON CONFLICT DO NOTHING"
            sql = f"INSERT INTO {t} ({collist}) VALUES %s{conflict}"
            psycopg2.extras.execute_values(pgc, sql, [tuple(r) for r in rows])
        # reset identity sequence for id-PK tables
        if "id" in cols:
            try:
                pgc.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")
            except Exception as exc:
                print(f"  (seq reset skipped for {t}: {exc})")
        pg.commit()
        pgc.execute(f"SELECT COUNT(*) FROM {t}")
        dst = pgc.fetchone()[0]
        summary.append((t, src_n, dst))

    print("\nTable                         source   dest")
    print("-" * 48)
    for t, s, d in summary:
        flag = "" if s <= d else "  <-- MISMATCH"
        print(f"{t:28} {s:>6} {d:>6}{flag}")
    src.close(); pg.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
