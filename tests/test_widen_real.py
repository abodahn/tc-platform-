"""widen_real_columns: preserve float4 text values with one transaction per table."""
from app.db import widen_real_columns


class _Conn:
    def __init__(self, cols, fail=()):
        self.cols, self.fail, self.sql, self.commits, self.rollbacks = cols, set(fail), [], 0, 0

    def execute(self, sql, *a):
        if sql.startswith("SELECT"):
            rows = [{"table_name": t, "column_name": c} for t, c in self.cols]
            return type("R", (), {"fetchall": lambda _s: rows})()
        self.sql.append(sql)
        if any(f in sql for f in self.fail):
            raise RuntimeError("boom")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_widens_each_table_and_survives_one_failure():
    c = _Conn([("pr_items", "unit_price"), ("bad", "x"), ("pr_items", 'to"tal'),
               ("bad", "y"), ('pr_"requests', "total")], fail=['"bad"'])
    assert widen_real_columns(c) == 3
    assert len(c.sql) == 3
    assert c.sql == [
        'ALTER TABLE "pr_items" ALTER COLUMN "unit_price" TYPE DOUBLE PRECISION USING "unit_price"::text::double precision, ALTER COLUMN "total" TYPE DOUBLE PRECISION USING "total"::text::double precision',
        'ALTER TABLE "bad" ALTER COLUMN "x" TYPE DOUBLE PRECISION USING "x"::text::double precision, ALTER COLUMN "y" TYPE DOUBLE PRECISION USING "y"::text::double precision',
        'ALTER TABLE "pr_requests" ALTER COLUMN "total" TYPE DOUBLE PRECISION USING "total"::text::double precision',
    ]
    assert (c.commits, c.rollbacks) == (2, 1)


def test_nothing_to_do():
    c = _Conn([])
    assert widen_real_columns(c) == 0
    assert (c.sql, c.commits, c.rollbacks) == ([], 0, 0)
