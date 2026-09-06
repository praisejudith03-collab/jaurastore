"""Offline tests for push_catalog_to_supabase.py.

The one-shot that pushes the whole live catalogue into the Supabase
products table after the PR-38 dedupe fix. It must select exactly
catalog.merged(include_hidden=True) (offline rows included, soft-deleted
already excluded by merged()), page it 200 at a time, upsert through
supabase_store.upsert_products ONLY - never replace_all_products, never
delete, no reset - print sent vs reported, and exit 1 on any hard failure.

Run with:  python3 -m pytest tests/test_push_catalog.py -q
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")  # never the real shop
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

import pytest  # noqa: E402

import push_catalog_to_supabase as push  # noqa: E402
import supabase_store as sb  # noqa: E402


def _enable(monkeypatch):
    """Fake a configured Supabase with a live client."""
    monkeypatch.setattr(push, "Config",
                        SimpleNamespace(SUPABASE_URL="u",
                                        SUPABASE_SERVICE_ROLE_KEY="k"))
    monkeypatch.setattr(sb, "client", lambda: object())


def test_select_asks_merged_for_hidden_rows_and_sorts_by_id(monkeypatch):
    import catalog as catalog_mod
    calls = []
    fake = [{"id": "c-3", "name": "C"},
            {"id": "a-1", "name": "A", "online": False},   # offline: still pushed
            {"id": "b-2", "name": "B"}]
    def fake_merged(include_hidden=False):
        calls.append(include_hidden)
        return fake
    monkeypatch.setattr(catalog_mod, "merged", fake_merged)
    out = push.select_products()
    assert calls == [True], "offline products must be pushed, so include_hidden=True"
    assert [p["id"] for p in out] == ["a-1", "b-2", "c-3"], \
        "selection must be deterministic (sorted by id) for stable page boundaries"


def test_select_drops_rows_without_an_id(monkeypatch):
    import catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, "merged",
                        lambda include_hidden=False:
                        [{"id": "ok"}, {"id": ""}, {"name": "no id key"}])
    assert [p["id"] for p in push.select_products()] == ["ok"]


def test_select_returns_copies_not_live_objects(monkeypatch):
    import catalog as catalog_mod
    fake = [{"id": "x", "name": "X"}]
    monkeypatch.setattr(catalog_mod, "merged",
                        lambda include_hidden=False: fake)
    out = push.select_products()
    out[0]["name"] = "MUTATED"
    assert fake[0]["name"] == "X", "the script must not mutate merged() output"


def test_pages_of_200():
    rows = [{"id": i} for i in range(450)]
    pgs = push.pages(rows)
    assert [len(p) for p in pgs] == [200, 200, 50]
    assert pgs[0][0]["id"] == 0 and pgs[-1][-1]["id"] == 449
    assert push.pages([]) == []
    assert [len(p) for p in push.pages([{"id": i} for i in range(200)])] == [200]


def test_dry_run_prints_counts_and_never_writes(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("--dry-run must never call upsert_products")
    monkeypatch.setattr(sb, "upsert_products", boom)
    monkeypatch.setattr(push, "select_products",
                        lambda: [{"id": i} for i in range(450)])
    push.main(["--dry-run"])          # no SystemExit expected
    out = capsys.readouterr().out
    assert "450" in out, "the dry run must print how many products would go"
    assert "3" in out, "the dry run must print how many pages"
    assert "dry run" in out.lower()


def test_real_run_without_credentials_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(push, "Config",
                        SimpleNamespace(SUPABASE_URL="",
                                        SUPABASE_SERVICE_ROLE_KEY=""))
    monkeypatch.setattr(push, "select_products", lambda: [{"id": "p1"}])
    with pytest.raises(SystemExit) as e:
        push.main([])
    assert e.value.code == 1
    assert "SUPABASE" in capsys.readouterr().out


def test_page_failure_stops_and_exits_1_with_sent_vs_reported(monkeypatch, capsys):
    _enable(monkeypatch)
    monkeypatch.setattr(sb, "upsert_products", lambda rows: False)
    monkeypatch.setattr(push, "select_products",
                        lambda: [{"id": i} for i in range(450)])
    with pytest.raises(SystemExit) as e:
        push.main([])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "sent 200" in out and "stored 0" in out, \
        "the failure summary must show sent vs reported"


def test_full_push_prints_sent_vs_reported_and_succeeds(monkeypatch, capsys):
    _enable(monkeypatch)
    seen = []
    def fake_upsert(rows):
        seen.append(len(rows))
        return True
    monkeypatch.setattr(sb, "upsert_products", fake_upsert)
    monkeypatch.setattr(push, "select_products",
                        lambda: [{"id": i} for i in range(450)])
    push.main([])                      # no SystemExit expected
    assert seen == [200, 200, 50], "pages must go out 200 at a time"
    out = capsys.readouterr().out
    assert "sent 450" in out, "must print how many were sent"
    assert "450 stored" in out, "must print how many Supabase reported stored"


def test_script_source_never_replaces_or_deletes():
    """Rule: upserts only - no replace_all_products, no deletes, no reset."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "push_catalog_to_supabase.py"),
        encoding="utf-8").read()
    code = src.split('"""', 2)[-1]     # module docstring may quote the words
    assert "replace_all_products" not in code
    assert "delete_products" not in code
    assert ".delete(" not in code
    assert "--reset" not in code
