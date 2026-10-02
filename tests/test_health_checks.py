"""Health endpoints fail closed on a hung DB/runtime probe, within Render's budget."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import health_checks  # noqa: E402
import supabase_store  # noqa: E402
from config import Config  # noqa: E402
from db import init_db  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_URL", "")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
    init_db()
    app = appmod.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_health_routes_ping_the_database_and_runtime(client):
    for path in ("/healthz", "/api/health"):
        response = client.get(path)
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["ok"] is True
        assert body["database"]["ok"] is True
        assert body["database"]["checks"]["sqlite"]["ok"] is True
        assert body["eventLoop"]["ok"] is True
        assert "background" in body
        assert "no-store" in response.headers.get("Cache-Control", "")


def test_hung_supabase_probe_returns_http_500_before_five_seconds(client, monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(health_checks, "DATABASE_PING_TIMEOUT_SECONDS", 0.1)

    def hang():
        time.sleep(0.5)
        return "ok"

    monkeypatch.setattr(supabase_store, "ping", hang)
    started = time.monotonic()
    response = client.get("/api/health")
    elapsed = time.monotonic() - started

    body = response.get_json()
    assert response.status_code == 500, body
    assert body["ok"] is False
    assert body["database"]["checks"]["supabase"]["ok"] is False
    assert "timed out" in body["database"]["checks"]["supabase"]["error"]
    assert elapsed < 1.0, f"health response took {elapsed:.2f}s"
    # Let the isolated daemon probe finish before the next test reuses the guard.
    time.sleep(0.45)


def test_unreachable_supabase_fails_both_health_endpoints(client, monkeypatch):
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(supabase_store, "ping", lambda: "unreachable")
    for path in ("/healthz", "/api/health"):
        response = client.get(path)
        assert response.status_code == 500
        assert response.get_json()["database"]["ok"] is False


def test_runtime_scheduling_failure_is_unhealthy(client, monkeypatch):
    monkeypatch.setattr(health_checks, "_event_loop_probe",
                        lambda: {"ok": False, "latencyMs": 251,
                                 "error": "runtime scheduling probe timed out"})
    response = client.get("/healthz")
    assert response.status_code == 500
    assert response.get_json()["eventLoop"]["ok"] is False


def test_render_health_path_and_worker_watchdog_are_configured():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    procfile = (ROOT / "Procfile").read_text(encoding="utf-8")
    assert "healthCheckPath: /healthz" in render
    assert "--timeout 45" in render
    assert "--timeout 45" in procfile
