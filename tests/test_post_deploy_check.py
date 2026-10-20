"""The post-deploy verifier must catch exactly what it claims to catch.

`tools/post_deploy_check.py` is the only check that runs against the deployed
service, so it is tested the same way as the rest of the suite: a canned HTTP
server drives every failure branch deterministically, and one end-to-end test
points it at the real Flask app over a real socket.

A verifier that silently passes is worse than no verifier, which is why each
test below breaks ONE thing and asserts the specific check fails.

Run with:  python3 -m pytest tests/test_post_deploy_check.py -q
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DB_PATH", "/tmp/jaura_post_deploy.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_post_deploy_catalog.json")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")

import post_deploy_check as pdc  # noqa: E402


# --------------------------------------------------------------- canned server
def _html(lang="fr", tokens=("219",), grid=True):
    scripts = "".join(f'<script src="js/app.js?v={t}"></script>' for t in tokens)
    grid_markup = '<div class="product-grid" data-shop-grid></div>' if grid else ""
    return (f'<!doctype html><html lang="{lang}"><head></head><body>'
            f'{scripts}{grid_markup}</body></html>').encode()


def _healthy_routes():
    return {
        "/healthz": (200, json.dumps({"ok": True, "latencyMs": 1.1,
                                      "database": {"cached": True}}).encode()),
        "/api/catalog": (200, json.dumps({
            "ok": True,
            "products": [{"id": "a"}, {"id": "b"}],
            "meta": {"count": 3, "publishedCount": 2, "hiddenCount": 1},
        }).encode()),
        "/api/site": (200, json.dumps({"ok": True, "site": {"cfaRate": 0.44,
                                                            "store_active": True}}).encode()),
        "/shop.html": (200, _html()),
        "/api/admin/catalog/audit": (401, b"{}"),
    }


class _Handler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):                                  # noqa: N802 - stdlib API
        path = self.path.split("?", 1)[0]
        handler = self.routes.get(path)
        if handler is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        result = handler() if callable(handler) else handler
        status, body = result[0], result[1]
        headers = result[2] if len(result) > 2 else {}
        headers.setdefault("Content-Type", "text/html; charset=utf-8")
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):                     # keep pytest output clean
        return


@pytest.fixture()
def site_server():
    handler = type("BoundHandler", (_Handler,), {"routes": _healthy_routes()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run(base, *extra):
    return pdc.main(["--base", base, "--timeout", "5", *extra])


# ----------------------------------------------------------------- good deploy
def test_a_healthy_deployment_passes(site_server, capsys):
    base, _handler = site_server
    assert _run(base, "--samples", "3") == 0
    out = capsys.readouterr().out
    for name in pdc.CHECKS:
        assert f"PASS  {name}" in out
    assert "POST-DEPLOY CHECKS PASSED" in out


def test_the_json_report_is_machine_readable(site_server, capsys):
    base, _handler = site_server
    assert _run(base, "--samples", "2", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert [c["check"] for c in report["checks"]] == list(pdc.CHECKS)
    assert report["elapsedMs"] >= 0


# ---------------------------------------------------------------- each failure
def test_a_dropped_row_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/catalog"] = (200, json.dumps({
        "ok": True, "products": [{"id": "a"}],
        "meta": {"count": 3, "publishedCount": 2, "hiddenCount": 1},
    }).encode())
    assert _run(base, "--checks", "catalog") == 1
    assert "publishedCount=2 but 1 products were served" in capsys.readouterr().out


def test_an_api_without_the_visibility_contract_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/catalog"] = (200, json.dumps({
        "ok": True, "products": [{"id": "a"}], "meta": {"count": 1},
    }).encode())
    assert _run(base, "--checks", "catalog") == 1
    assert "predates the visibility contract" in capsys.readouterr().out


def test_duplicate_ids_are_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/catalog"] = (200, json.dumps({
        "ok": True, "products": [{"id": "a"}, {"id": "a"}],
        "meta": {"count": 2, "publishedCount": 2, "hiddenCount": 0},
    }).encode())
    assert _run(base, "--checks", "catalog") == 1
    assert "duplicated product id" in capsys.readouterr().out


def test_a_zero_exchange_rate_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/site"] = (200, json.dumps({
        "ok": True, "site": {"cfaRate": 0, "store_active": True}}).encode())
    assert _run(base, "--checks", "site") == 1
    assert "cfaRate is 0" in capsys.readouterr().out


def test_unreadable_site_settings_name_the_reason(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/site"] = (503, json.dumps({
        "ok": False, "error": "Site settings are temporarily unavailable."}).encode())
    assert _run(base, "--checks", "site") == 1
    assert "Site settings are temporarily unavailable." in capsys.readouterr().out


def test_a_slow_health_probe_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/healthz"] = (200, json.dumps({
        "ok": True, "latencyMs": 120.0, "database": {"cached": True}}).encode())
    assert _run(base, "--checks", "healthz", "--samples", "4") == 1
    assert "0% under 50 ms" in capsys.readouterr().out


def test_one_cold_probe_does_not_fail_a_warm_service(site_server, capsys):
    """4 of 5 samples inside the budget is exactly the documented threshold."""
    base, handler = site_server
    samples = iter([90.0, 1.0, 1.0, 1.0, 1.0])

    def healthz():
        return (200, json.dumps({"ok": True, "latencyMs": next(samples),
                                 "database": {"cached": True}}).encode())

    handler.routes["/healthz"] = healthz
    assert _run(base, "--checks", "healthz", "--samples", "5") == 0
    assert "80% under 50 ms" in capsys.readouterr().out


def test_an_unhealthy_health_document_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/healthz"] = (503, json.dumps({
        "ok": False, "latencyMs": 1.0,
        "database": {"error": "sqlite ping failed"}}).encode())
    assert _run(base, "--checks", "healthz", "--samples", "1") == 1
    assert "ok=false" in capsys.readouterr().out


def test_a_page_that_is_not_french_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/shop.html"] = (200, _html(lang="en"))
    assert _run(base, "--checks", "shop") == 1
    assert 'lang=en for a French device' in capsys.readouterr().out


def test_a_half_bumped_build_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/shop.html"] = (200, _html(tokens=("219", "218")))
    assert _run(base, "--checks", "shop") == 1
    assert "mixed asset tokens" in capsys.readouterr().out


def test_a_page_without_the_grid_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/shop.html"] = (200, _html(grid=False))
    assert _run(base, "--checks", "shop") == 1
    assert "grid container is missing" in capsys.readouterr().out


def test_a_redirect_is_gated_not_leaked(site_server, capsys):
    """A 302 to the login page is an unauthenticated caller being turned away."""
    base, handler = site_server
    handler.routes["/api/admin/catalog/audit"] = (
        302, b"", {"Location": "/admin.html?next=/api/admin/catalog/audit"})
    handler.routes["/admin.html"] = (200, _html(lang="fr"))
    assert _run(base, "--checks", "audit") == 0
    assert "admin-only (HTTP 302 to /admin.html" in capsys.readouterr().out


def test_a_shop_page_that_redirects_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/"] = (200, _html())
    handler.routes["/shop.html"] = (
        302, b"", {"Location": "/"})
    assert _run(base, "--checks", "shop") == 1
    assert "redirected to" in capsys.readouterr().out


def test_a_public_audit_endpoint_is_caught(site_server, capsys):
    base, handler = site_server
    handler.routes["/api/admin/catalog/audit"] = (200, b'{"ok": true}')
    assert _run(base, "--checks", "audit") == 1
    assert "leaks the catalogue" in capsys.readouterr().out


def test_an_old_build_without_the_audit_route_is_caught(site_server, capsys):
    base, handler = site_server
    del handler.routes["/api/admin/catalog/audit"]
    assert _run(base, "--checks", "audit") == 1
    assert "predates the release" in capsys.readouterr().out


def test_an_unreachable_service_exits_two():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert pdc.main(["--base", f"http://127.0.0.1:{port}", "--timeout", "1",
                     "--checks", "healthz,catalog"]) == 2


def test_a_repeated_check_name_runs_once(site_server, capsys):
    base, _handler = site_server
    assert _run(base, "--checks", "site,SITE") == 0
    assert capsys.readouterr().out.count("PASS  site") == 1


def test_an_unknown_check_is_rejected_before_any_request(site_server, capsys):
    base, _handler = site_server
    with pytest.raises(SystemExit) as raised:
        _run(base, "--checks", "healthz,nope")
    assert raised.value.code == 2
    assert "unknown check(s): nope" in capsys.readouterr().err


# ----------------------------------------------------------- the workflow job
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "post-deploy.yml")


def _workflow():
    yaml = pytest.importorskip("yaml")
    with open(WORKFLOW, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_the_workflow_cannot_swallow_a_failed_check():
    """A verifier that reports red in a green job is worse than none.

    Three ways this job could lie: run on a schedule (Render hiccups become
    red builds), allow failure (continue-on-error), or pipe the verifier into
    something that replaces its exit status. None may come back.
    """
    data = _workflow()
    job = data["jobs"]["verify"]
    assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] <= 15
    assert not job.get("continue-on-error")
    script = next(step["run"] for step in job["steps"] if "post_deploy_check" in step.get("run", ""))
    assert "| tee" not in script and "|tee" not in script
    assert "|| true" not in script
    assert "status=$?" in script and 'exit "$status"' in script


def test_the_workflow_is_manual_only_and_credential_free():
    data = _workflow()
    on = data.get(True) or data["on"]
    triggers = set(on) if isinstance(on, dict) else set(on)
    assert triggers == {"workflow_dispatch"}, (
        "a scheduled production probe duplicates UptimeRobot and turns a "
        "Render restart into a red build")
    text = open(WORKFLOW, encoding="utf-8").read()
    for secret in ("SUPABASE", "secrets.", "DATABASE_URL", "SERVICE_ROLE"):
        assert secret not in text, f"the verifier must stay read-only: {secret}"


# ------------------------------------------------------------- the real app
_LAUNCHER = """
import sys
from werkzeug.serving import make_server
from app import create_app

app = create_app()
server = make_server("127.0.0.1", 0, app, threaded=True)
print("PORT", server.server_port, flush=True)
server.serve_forever()
"""


def _start_app(tmp_path):
    """Boot the shipped app in its own process, on a socket, in a clean env.

    In-process would inherit whatever the rest of the suite has done to the
    shared modules (one test deliberately sets Config.ENV = "production", which
    makes /api/site answer 503 for want of Supabase credentials). A subprocess
    is the only way to assert what a deploy actually is: this code, this env.
    """
    import subprocess
    import time

    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("SUPABASE_", "RENDER_", "DATABASE_"))}
    env.update({
        "FLASK_ENV": "testing",
        "SECRET_KEY": "post-deploy-check-secret",
        "DB_PATH": str(tmp_path / "app.db"),
        "CATALOG_PATH": str(tmp_path / "catalog.json"),
        "CATEGORIES_PATH": str(tmp_path / "categories.json"),
        "ADMIN_EMAILS": "jaurastore@gmail.com",
        "SCHEDULER_ENABLED": "0",
        "KEEP_ALIVE": "0",
    })
    proc = subprocess.Popen([sys.executable, "-c", _LAUNCHER], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    seen = []
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            seen.append(line)
            if line.startswith("PORT "):
                return proc, f"http://127.0.0.1:{int(line.split()[1])}"
        raise AssertionError("the app did not start:\n" + "".join(seen))
    except Exception:
        proc.kill()
        raise


def test_the_real_app_passes_the_verifier(tmp_path):
    """End to end: the shipped Flask app, a real socket, every check green."""
    proc, base = _start_app(tmp_path)
    try:
        assert _run(base, "--samples", "3") == 0, f"checks failed against {base}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
