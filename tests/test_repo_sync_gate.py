"""Only a deployed instance may publish catalogue state to the repository.

A local preview run once regenerated data/catalog.json and
js/products-data.js from its scratch catalogue and pushed them to main
(wix-001's recorded stock went 24 -> 0; the revert is on this branch's
history). repo_sync.repo_sync_blocked_reason() closes that door
structurally: every publish path - the automatic post-write sync
(catalog._sync_repo_async), the nightly backup (backup.run) and the manual
"Sync to GitHub" button (POST /api/admin/sync/repo) - consults the one gate,
and only FLASK_ENV=production|staging with REPO_SYNC_ON_WRITE on, outside
pytest, gets through.

The HTTP tests drive the owner-facing consequences over the real app: the
button answers 409 with the reason off a deployed instance, and an admin
product save must not spawn a sync thread in that situation either.

Run with:  python3 -m pytest tests/test_repo_sync_gate.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402
import backup as backupmod  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import repo_sync  # noqa: E402
from db import execute, init_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw import PW  # noqa: E402

os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = PW

EMAIL = "jaurastore@gmail.com"

# conftest's autouse _tracked_repo_is_read_only swaps repo_sync._commit_and_push
# for a no-op around EVERY test. The last-line-of-defence test below needs the
# real function, which is still unpatched at collection time.
_REAL_COMMIT_AND_PUSH = repo_sync._commit_and_push


@pytest.fixture(scope="module")
def app():
    a = appmod.create_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture()
def client(app):
    init_db()
    execute("DELETE FROM rate_limits")
    with app.test_client() as c:
        yield c


def login(client):
    r = client.post("/api/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.data
    return r.get_json()["csrf"]


# ----------------------------------------------------------------- the gate
class TestGate:
    def test_dev_environment_is_blocked(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        reason = repo_sync.repo_sync_blocked_reason()
        assert reason
        assert "production" in reason and "staging" in reason

    def test_unset_environment_is_blocked(self, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "", raising=False)
        reason = repo_sync.repo_sync_blocked_reason()
        assert reason and "production" in reason

    def test_staging_is_allowed(self, monkeypatch):
        # Simulate a real deployed process: not pytest.
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "staging", raising=False)
        monkeypatch.setattr(config_mod.Config, "REPO_SYNC_ON_WRITE", True,
                            raising=False)
        assert repo_sync.repo_sync_blocked_reason() is None

    def test_production_is_allowed(self, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "production", raising=False)
        monkeypatch.setattr(config_mod.Config, "REPO_SYNC_ON_WRITE", True,
                            raising=False)
        assert repo_sync.repo_sync_blocked_reason() is None

    def test_production_with_sync_disabled_is_blocked(self, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "production", raising=False)
        monkeypatch.setattr(config_mod.Config, "REPO_SYNC_ON_WRITE", False,
                            raising=False)
        reason = repo_sync.repo_sync_blocked_reason()
        assert reason and "REPO_SYNC_ON_WRITE" in reason

    def test_pytest_is_blocked_even_in_production(self, monkeypatch):
        # The suite itself IS pytest, so the env-var detector must fire even
        # with a deployed-looking environment. This file also relies on
        # conftest's _tracked_repo_is_read_only fixture, which is the
        # second line of defence.
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "production", raising=False)
        monkeypatch.setattr(config_mod.Config, "REPO_SYNC_ON_WRITE", True,
                            raising=False)
        reason = repo_sync.repo_sync_blocked_reason()
        assert reason and "test" in reason.lower()

    def test_regenerate_refuses_off_deployed(self, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        # The module entry point honours the gate too - CLI or not.
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        called = {"n": 0}

        def _boom(**kw):
            called["n"] += 1
            return True, {}

        monkeypatch.setattr(repo_sync, "_commit_and_push", _boom)
        ok, report = repo_sync.regenerate(commit=True, push=True)
        assert ok and called["n"] == 0
        assert report.get("pushed") is False and "blocked" in (report.get("note") or "")

    def test_commit_and_push_itself_refuses_under_pytest(self, monkeypatch):
        """Last line of defence: a sync may reach _commit_and_push from a
        background thread spawned before other tests' monkeypatches were torn
        back down. The commit step re-checks at execution time, so no path
        through the module can ever produce a git commit during a test run."""
        monkeypatch.setattr(repo_sync, "_resolve_repo", lambda: "/nonexistent")
        git_calls = {"n": 0}

        def _no_git(*a, **k):
            git_calls["n"] += 1
            return True, ""

        monkeypatch.setattr(repo_sync, "_git", _no_git)
        ok, report = _REAL_COMMIT_AND_PUSH(True, True, "attempted", {})
        assert ok is False
        assert git_calls["n"] == 0
        assert report["committed"] is False and report["pushed"] is False
        assert "test suite" in report["note"]


# ------------------------------------------- automatic post-write sync path
class TestPostWriteSyncPath:
    def test_save_spawns_no_sync_off_deployed(self, client, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        tok = login(client)
        spawned = {"n": 0}
        real_thread = catalog_mod.threading.Thread

        class _NoThread:
            def __init__(self, *a, **k):
                spawned["n"] += 1

            def start(self):
                spawned["n"] += 100

        monkeypatch.setattr(catalog_mod, "threading",
                            type("T", (), {"Thread": _NoThread}))
        r = client.post("/api/admin/products",
                        json={"id": "gate-prod-1", "name": "Gate test product",
                              "priceNgn": 1500},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        assert spawned["n"] == 0, "a dev preview must never spawn a repo sync"

    def test_save_publishes_when_deployed(self, client, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "production", raising=False)
        tok = login(client)
        ran = {"regenerate": 0}

        class _FakeThread:
            def __init__(self, target=None, daemon=None, **k):
                self._target = target

            def start(self):
                ran["regenerate"] += 1
                # run inline under the guarded monkeypatched regenerate
                self._target()

        monkeypatch.setattr(catalog_mod, "threading",
                            type("T", (), {"Thread": _FakeThread}))
        monkeypatch.setattr(repo_sync, "regenerate",
                            lambda **kw: (True, {"pushed": True}))
        r = client.post("/api/admin/products",
                        json={"id": "gate-prod-2", "name": "Deployed product",
                              "priceNgn": 2500},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        assert ran["regenerate"] == 1


# ------------------------------------------------------- nightly backup path
class TestBackupPath:
    def test_backup_skips_repo_push_but_still_dumps_orders(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        monkeypatch.setattr(backupmod, "dump_orders", lambda *a, **k: 3)
        pushed = {"n": 0}
        monkeypatch.setattr(repo_sync, "regenerate",
                            lambda **kw: pushed.__setitem__("n", pushed["n"] + 1)
                            or (True, {}))
        ok, report = backupmod.run()
        assert ok
        assert pushed["n"] == 0
        assert report["orders"] == 3
        assert "skipped" in (report.get("repoSync") or "")

    def test_backup_pushes_when_deployed(self, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "staging", raising=False)
        monkeypatch.setattr(backupmod, "dump_orders", lambda *a, **k: 3)
        pushed = {"n": 0}
        monkeypatch.setattr(repo_sync, "regenerate",
                            lambda **kw: pushed.__setitem__("n", pushed["n"] + 1)
                            or (True, {"pushed": True}))
        ok, report = backupmod.run()
        assert ok and pushed["n"] == 1


# ------------------------------------------------- manual "Sync to GitHub"
class TestManualSyncButton:
    def test_button_answers_409_with_reason_off_deployed(self, client,
                                                         monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        tok = login(client)
        called = {"n": 0}
        monkeypatch.setattr(repo_sync, "regenerate",
                            lambda **kw: called.__setitem__("n", called["n"] + 1)
                            or (True, {}))
        r = client.post("/api/admin/sync/repo", headers={"X-CSRF-Token": tok})
        assert r.status_code == 409, r.data
        body = r.get_json()
        assert body["ok"] is False and body["blocked"] is True
        assert "production" in body["error"]
        assert called["n"] == 0, "the gated button must not reach regenerate()"

    def test_button_proceeds_when_deployed(self, client, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "staging", raising=False)
        tok = login(client)
        monkeypatch.setattr(repo_sync, "regenerate",
                            lambda **kw: (True, {"pushed": False,
                                                 "committed": False,
                                                 "note": "nothing to do"}))
        r = client.post("/api/admin/sync/repo", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.data
        assert r.get_json()["ok"] is True

    def test_status_surfaces_the_block_reason(self, client, monkeypatch):
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "development", raising=False)
        tok = login(client)
        r = client.get("/api/admin/sync/status", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["syncBlockedReason"]
        assert "production" in body["syncBlockedReason"]


# ------------------------------------- the tracked files stay byte-identical
class TestTrackedFilesUntouched:
    def test_regenerate_writes_only_the_scratch_root(self, monkeypatch,
                                                     scratch_repo_root,
                                                     tmp_path):
        # Even a production-allowed regenerate must write REPO_ROOT (the
        # scratch copy conftest hands every test), never the real checkout.
        monkeypatch.setattr(repo_sync, "_running_under_pytest", lambda: False)
        import config as config_mod
        monkeypatch.setattr(config_mod.Config, "ENV", "production", raising=False)
        monkeypatch.setattr(repo_sync, "REPO_ROOT", scratch_repo_root)
        monkeypatch.setattr(repo_sync, "_commit_and_push",
                            lambda **kw: (True, {"committed": False,
                                                 "pushed": False}))
        ok, report = repo_sync.regenerate(commit=True, push=True)
        assert ok, report
        # the checkout's own files are byte-identical (conftest asserts this
        # after every test; assert the scratch copy got the write instead)
        assert os.path.exists(os.path.join(scratch_repo_root,
                                           "js", "products-data.js"))
