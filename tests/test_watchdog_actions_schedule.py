"""Watchdog schedule + runtime placement, pinned as shipped.

Owner directive 2026-10-19:
  * live supplier updates every 20 minutes on GitHub Actions;
  * full deep audit nightly at 02:00 local server time (UTC+1 -> 01:00 UTC);
  * no supplier polling on the Render web service.

Run with: python3 -m pytest tests/test_watchdog_actions_schedule.py -q
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / ".github" / "workflows" / "supplier-watchdog.yml"
AUDIT = ROOT / ".github" / "workflows" / "catalog-watchdog.yml"
RENDER = ROOT / "render.yaml"
TOOL = ROOT / "tools" / "supplier_sync.py"


def _workflow(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _crons(path):
    # PyYAML parses the bare `on:` key as boolean True (YAML 1.1).
    doc = _workflow(path)
    on = doc.get(True, doc.get("on"))
    return [item["cron"] for item in on["schedule"]]


def test_live_supplier_updates_run_every_twenty_minutes_on_actions():
    crons = _crons(LIVE)
    # We offset the minutes by five so a live run cannot collide with the
    # 01:00 deep audit in the same concurrency group.
    assert "5,25,45 * * * *" in crons
    assert all(int(minute) % 20 == 5 for minute in crons[0].split()[0].split(","))


def test_full_catalogue_deep_audit_runs_at_two_am_local_server_time():
    # 01:00 UTC == 02:00 Africa/Porto-Novo (the shop's UTC+1 server clock).
    assert "0 1 * * *" in _crons(LIVE)
    assert "0 1 * * *" in _crons(AUDIT)


def test_supplier_polling_is_not_hosted_on_render():
    text = RENDER.read_text(encoding="utf-8")
    assert 'key: SUPPLIER_WATCHDOG_ENABLED' in text
    assert 'value: "0"' in text.split('key: SUPPLIER_WATCHDOG_ENABLED', 1)[1][:80]
    assert "GitHub Actions" in text


def test_actions_runner_exists_and_can_do_a_deep_audit():
    assert TOOL.exists(), "tools/supplier_sync.py is the Actions entry point"
    body = TOOL.read_text(encoding="utf-8")
    assert "--nightly" in body
    assert "load_state()" in body and "save_state()" in body
    workflow = LIVE.read_text(encoding="utf-8")
    assert "tools/supplier_sync.py" in workflow
    assert "actions/cache/restore" in workflow and "actions/cache/save" in workflow
    assert "SUPPLIER_WATCHDOG_STATE" in workflow


def test_the_in_process_scheduler_still_has_its_two_am_slot_for_housekeeping():
    src = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert 'NIGHTLY_HOUR = int(os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY_HOUR", "2")' in src
    assert "nightly_sweep" in src
    assert "_storage_sweeper" in src
