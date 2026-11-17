"""widen_real_columns: ALTERs each PG REAL column to DOUBLE PRECISION, one transaction each."""
from app.db import widen_real_columns


class _Conn:
    def __init__(self, cols, fail=()):
        self.cols, self.fail, self.sql, self.commits, self.rollbacks = cols, set(fail), [], 0, 0

    def execute(self, sql, *a):
        if sql.startswith("SELECT"):
            rows = [{"table_name": t, "column_name": c} for t, c in self.cols]
            return type("R", (), {"fetchall": lambda _s: rows})()
        if any(f in sql for f in self.fail):
            raise RuntimeError("boom")
        self.sql.append(sql)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_widens_each_column_and_survives_one_failure():
    c = _Conn([("pr_requests", "total"), ("pr_items", "unit_price"), ("bad", "x")], fail=['"bad"'])
    assert widen_real_columns(c) == 2
    assert c.sql[0] == 'ALTER TABLE "pr_requests" ALTER COLUMN "total" TYPE DOUBLE PRECISION'
    assert (c.commits, c.rollbacks) == (2, 1)


def test_nothing_to_do():
    assert widen_real_columns(_Conn([])) == 0
