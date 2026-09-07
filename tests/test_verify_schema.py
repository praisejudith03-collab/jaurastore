"""verify_schema.py - the pre-flight schema check the migration workflow runs.

The migration depends on the schema being applied first, so the check has to
actually fail when a table or column is missing. These tests drive check_live()
with a fake PostgREST client rather than asserting on the file's prose.

Run with:  python3 -m pytest tests -q
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify_schema as vs  # noqa: E402

SECRET = "eyJfake.service_role.key-value-that-must-never-be-printed"


class _Res:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Mimics PostgREST: selecting a column the table does not have errors."""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        self._select = None
        self._limit = 1

    def select(self, cols):
        self._select = cols
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._name not in self._owner.schema:
            raise RuntimeError(f'relation "public.{self._name}" does not exist')
        have = self._owner.schema[self._name]
        if self._select and self._select != "*":
            for col in self._select.split(","):
                if col.strip().strip('"') not in have:
                    raise RuntimeError(
                        f'column {self._name}.{col.strip().strip(chr(34))} does not exist')
        return _Res([{c: None for c in have}])


class FakeClient:
    def __init__(self, schema):
        self.schema = schema

    def table(self, name):
        return FakeTable(self, name)


def _complete_schema():
    """Every required table with at least the columns verify_schema probes."""
    out = {}
    for t in vs.REQUIRED_TABLES:
        cols = vs.REQUIRED_COLUMNS.get(t)
        if cols:
            out[t] = {c.strip().strip('"') for c in cols} | {"id"}
        else:
            out[t] = {"id"}
    return out


def test_a_fully_applied_schema_passes():
    rep = vs.check_live(FakeClient(_complete_schema()))
    assert rep["ok"] is True
    assert rep["missing_tables"] == []
    assert len(rep["tables"]) == len(vs.REQUIRED_TABLES) == 13


def test_a_missing_table_is_reported_by_name():
    schema = _complete_schema()
    del schema["coupon_uses"]
    del schema["delivery_zones"]
    rep = vs.check_live(FakeClient(schema))
    assert rep["ok"] is False
    assert sorted(rep["missing_tables"]) == ["coupon_uses", "delivery_zones"]
    assert rep["tables"]["coupon_uses"]["present"] is False
    # a missing table must not be confused with an empty one
    assert rep["tables"]["products"]["present"] is True


def test_a_missing_review_column_is_reported():
    """The exact regression this file exists to catch: product_reviews shipped
    as stars/note/at, and the application contract is rating/title/body."""
    schema = _complete_schema()
    schema["product_reviews"] = {"id", "product_id", "email", "name", "stars",
                                 "note", "at", "hidden"}
    rep = vs.check_live(FakeClient(schema))
    assert rep["ok"] is False, "the superseded column names must fail the check"
    assert "product_reviews" in rep["missing_tables"]
    assert "does not exist" in rep["tables"]["product_reviews"]["error"]


def test_a_missing_products_column_is_reported():
    schema = _complete_schema()
    schema["products"].discard("stock_quantity")
    rep = vs.check_live(FakeClient(schema))
    assert rep["ok"] is False
    assert "products" in rep["missing_tables"]


def test_the_required_column_inventory_covers_the_agreed_contract():
    """Guard against verify_schema.py drifting from the schema itself."""
    assert vs.REQUIRED_COLUMNS["product_reviews"] == (
        "product_id", "email", "name", "rating", "title", "body", "hidden",
        "created_at", "updated_at")
    assert len(vs.REQUIRED_TABLES) == 13
    assert len(vs.REQUIRED_COLUMNS["products"]) == 15


def test_constraints_are_checked_against_the_sql_that_is_applied():
    got = {c["table"]: c for c in vs.check_constraints()}
    assert got["coupon_uses"]["present"] is True
    assert got["product_reviews"]["present"] is True


def test_a_removed_constraint_fails_the_check():
    text = "create table if not exists coupon_uses (code text, order_id text);"
    got = {c["table"]: c for c in vs.check_constraints(text)}
    assert got["coupon_uses"]["present"] is False
    assert got["product_reviews"]["present"] is False


def test_the_report_never_contains_the_service_role_key(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SECRET)
    out = tmp_path / "schema.json"
    # client() is None without the real library configured, so the probe is
    # skipped - but the report is still written, which is the leak surface.
    import supabase_store
    monkeypatch.setattr(supabase_store, "client", lambda: None)
    code = vs.main(["--json", str(out)])
    assert code == 1, "an unavailable client must not be reported as ok"
    printed = capsys.readouterr().out
    written = out.read_text(encoding="utf-8")
    assert SECRET not in printed, "the key was printed to the log"
    assert SECRET not in written, "the key was written into the report"
    assert "service_role" not in written.replace(SECRET, "")


def test_no_credentials_is_a_failure_not_a_pass(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    code = vs.main([])
    assert code == 1
    assert "unset" in capsys.readouterr().out


def test_main_passes_when_the_schema_is_complete(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SECRET)
    import supabase_store
    monkeypatch.setattr(supabase_store, "client",
                        lambda: FakeClient(_complete_schema()))
    assert vs.main([]) == 0
    out = capsys.readouterr().out
    assert "ok                       : True" in out
    assert SECRET not in out
