"""Regression coverage for background-job failure controls in admin.js."""
from pathlib import Path
import re

ADMIN_JS = (Path(__file__).parents[1] / "js" / "admin.js").read_text(encoding="utf-8")


def _func(name):
    start = ADMIN_JS.index(f"function {name}(")
    rest = ADMIN_JS[start:]
    match = re.search(r"\n(?:async )?function ", rest[1:])
    return rest[:match.start() + 1] if match else rest


def test_clear_all_uses_the_loaded_network_client_not_an_undefined_local():
    body = _func("clearAllCrashReports")
    assert 'window.JA_NET.api("api/admin/job-failures", { method: "DELETE" })' in body
    assert "await api(" not in body
    assert "fillCrashReports()" in body


def test_delete_one_report_uses_the_same_network_client():
    body = _func("renderCrashPage")
    assert 'window.JA_NET.api("api/admin/job-failures/"' in body
    assert "await api(" not in body
