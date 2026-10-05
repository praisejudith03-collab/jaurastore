"""Health endpoints fail closed on a hung DB/runtime probe, within Render's budget."""
import sys
import threading
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


# --- the stuck-probe regression: /healthz must never be poisoned for good ---


def _use_supabase(monkeypatch, ping_timeout):
    monkeypatch.setattr(Config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role")
    monkeypatch.setattr(health_checks, "DATABASE_PING_TIMEOUT_SECONDS", ping_timeout)


def _await_guard_cleared(timeout=3.0):
    deadline = time.monotonic() + timeout
    while health_checks._remote_probe_running and time.monotonic() < deadline:
        time.sleep(0.01)
    return not health_checks._remote_probe_running


def test_a_timed_out_probe_releases_the_guard_for_the_next_request(client, monkeypatch):
    """A probe that overruns its deadline must hand /healthz back, not hold it."""
    _use_supabase(monkeypatch, ping_timeout=0.1)
    gate = threading.Event()

    def stall_then_answer():
        gate.wait(3.0)
        return "ok"

    monkeypatch.setattr(supabase_store, "ping", stall_then_answer)
    first = health_checks._supabase_ping()
    assert first["ok"] is False
    assert "timed out" in first["error"]
    # The stalled thread has not returned yet, so the guard is still held.
    assert health_checks._remote_probe_running is True

    gate.set()
    assert _await_guard_cleared(), "the probe's finally clause never released the guard"

    # Once it returns, the very next probe measures for real instead of
    # answering "previous database probe is still pending".
    monkeypatch.setattr(supabase_store, "ping", lambda: "ok")
    second = health_checks._supabase_ping()
    assert "still pending" not in str(second.get("error")), second
    assert second["ok"] is True, second


def test_an_abandoned_probe_cannot_poison_healthz_forever(client, monkeypatch):
    """A probe that never returns is disowned, so the endpoint self-heals."""
    _use_supabase(monkeypatch, ping_timeout=0.05)
    # raising=False so that on a build without the staleness bound this test
    # fails on the behaviour it exists for - an endless "still pending" -
    # rather than on a missing attribute.
    monkeypatch.setattr(health_checks, "PROBE_STALE_AFTER_SECONDS", 0.2, raising=False)
    release = threading.Event()

    def hang_past_every_deadline():
        release.wait(5.0)
        return "ok"

    monkeypatch.setattr(supabase_store, "ping", hang_past_every_deadline)
    assert "timed out" in health_checks._supabase_ping()["error"]
    # Inside the staleness bound the guard still holds, as designed.
    assert "still pending" in health_checks._supabase_ping()["error"]

    # Past the bound a fresh probe is allowed rather than an endless stream
    # of "previous database probe is still pending".
    monkeypatch.setattr(supabase_store, "ping", lambda: "ok")
    outcome = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        outcome = health_checks._supabase_ping()
        if "still pending" not in str(outcome.get("error")):
            break
        time.sleep(0.05)
    assert "still pending" not in str(outcome.get("error")), outcome
    assert outcome["ok"] is True, outcome

    # The disowned thread finally returning must not clear the guard that now
    # belongs to its replacement, which is what would wedge /healthz again.
    release.set()
    time.sleep(0.3)
    healthy = health_checks._supabase_ping()
    assert "still pending" not in str(healthy.get("error")), healthy
    assert healthy["ok"] is True, healthy
    assert _await_guard_cleared()


def test_postgrest_requests_are_bounded_below_the_health_deadline():
    """The client timeout is what keeps a stalled read from pinning the probe."""
    saved = (supabase_store._client, supabase_store._loaded,
             supabase_store._probe_client, supabase_store._probe_loaded,
             Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY)
    probe = shop = None
    try:
        supabase_store._client, supabase_store._loaded = None, False
        supabase_store._probe_client, supabase_store._probe_loaded = None, False
        Config.SUPABASE_URL = "https://example.supabase.co"
        Config.SUPABASE_SERVICE_ROLE_KEY = "service-role"

        probe = supabase_store.probe_client()
        assert probe is not None
        probe_timeout = float(probe.postgrest.session.timeout.read)
        # The package default is 120s, which is what let one stalled read hold
        # the health probe - and therefore /healthz - for two minutes.
        assert probe_timeout < health_checks.DATABASE_PING_TIMEOUT_SECONDS, (
            f"health probe PostgREST read timeout {probe_timeout}s is not below "
            f"the {health_checks.DATABASE_PING_TIMEOUT_SECONDS}s health deadline")

        # The shop's own client keeps real headroom for cold free-tier reads,
        # but still stays inside the gunicorn worker timeout rather than the
        # package's 120s, which no worker can ever wait out.
        shop = supabase_store.client()
        assert shop is not None
        shop_timeout = float(shop.postgrest.session.timeout.read)
        assert shop_timeout < 45.0, (
            f"shop PostgREST read timeout {shop_timeout}s outlives the "
            f"gunicorn --timeout 45 worker")
        assert shop_timeout > probe_timeout, \
            "the shop's reads must not inherit the health check's tight budget"
    finally:
        # Close the real connection pools this test opened instead of leaving
        # them for the garbage collector; the suite has a wall-clock
        # performance test downstream that must not inherit this load.
        for built in (probe, shop):
            try:
                built.postgrest.session.close()
            except Exception:
                pass
        (supabase_store._client, supabase_store._loaded,
         supabase_store._probe_client, supabase_store._probe_loaded,
         Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY) = saved

    for timeout in (health_checks.DATABASE_PING_TIMEOUT_SECONDS,
                    supabase_store.SUPABASE_HTTP_TIMEOUT_SECONDS):
        options = supabase_store._client_options(timeout)
        assert options is not None, "no bounded client options on this supabase version"
        assert float(options.postgrest_client_timeout) == timeout
