"""
Deterministic unit tests for the SQLite->PostgreSQL translation shim.
These need no PostgreSQL server (pure string transforms), so they verify the
rewrites are correct even where psycopg2 / a live DB isn't available locally.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import translate_ddl, _translate_sql, _NO_ID_TABLES  # noqa: E402


def test_ddl_autoincrement_to_serial():
    out = translate_ddl("id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT", "pg")
    assert "SERIAL PRIMARY KEY" in out and "AUTOINCREMENT" not in out
    # sqlite dialect unchanged
    assert translate_ddl("id INTEGER PRIMARY KEY AUTOINCREMENT", "sqlite") == \
        "id INTEGER PRIMARY KEY AUTOINCREMENT"


def test_placeholders_and_insert_or_ignore():
    sql, was_ignore = _translate_sql("INSERT OR IGNORE INTO t (a,b) VALUES (?,?)")
    assert was_ignore is True
    assert sql == "INSERT INTO t (a,b) VALUES (%s,%s)"


def test_plain_select_placeholders():
    sql, was_ignore = _translate_sql("SELECT * FROM t WHERE a=? AND b=?")
    assert sql == "SELECT * FROM t WHERE a=%s AND b=%s" and was_ignore is False


def test_date_now():
    sql, _ = _translate_sql("SELECT 1 WHERE next_due < date('now')")
    assert "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD')" in sql
    assert "date('now')" not in sql


def test_datetime_now():
    sql, _ = _translate_sql("INSERT INTO t (created_at) VALUES (datetime('now'))")
    assert "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')" in sql


def test_date_now_with_interval_param():
    sql, _ = _translate_sql("SELECT 1 WHERE created_at >= date('now', ?)")
    assert "(%s)::interval" in sql and "to_char" in sql and "date('now'" not in sql


def test_settings_is_no_id_table():
    # mnt_settings has no `id` PK -> shim must NOT append RETURNING id
    assert "mnt_settings" in _NO_ID_TABLES
