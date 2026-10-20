#!/usr/bin/env python3
"""Supplier stock watchdog runner for GitHub Actions.

The Render web service owns the storefront, not a background worker. This
script is the *live update* and *nightly deep audit* host:

  * live mode (default) runs every 20 minutes from
    `.github/workflows/supplier-watchdog.yml`; it checks at most two unique
    supplier URLs and honours the one-hour per-URL cooldown;
  * --nightly runs once at 02:00 local server time (01:00 UTC for the shop's
    UTC+1 clock) and re-checks the whole linked catalogue with no cooldown.

State (which URL was fetched when) is carried between the fresh Actions
processes through SUPPLIER_WATCHDOG_STATE, so the rotation does not restart at
the same two products every run. Passing --no-state is useful for a one-off
diagnostic.

Exit codes: 0 = ran, 1 = the watchdog recorded a fatal read error, 2 = it
could not run at all (usually missing Supabase credentials).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Production source of truth when the Actions secrets are present. Config reads
# FLASK_ENV at import time, so this must happen before importing catalog.
if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("KEEP_ALIVE", "0")


def _summary_line(result):
    return ("supplier watchdog: links={links} checked={checked} updated={updated} "
            "warnings={warnings} locked={locked} at={at}".format(
                links=result.get("links", 0), checked=result.get("checked", 0),
                updated=result.get("updated", 0), warnings=result.get("warnings", 0),
                locked=result.get("locked", 0), at=result.get("at", "")))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the supplier stock watchdog")
    parser.add_argument("--nightly", action="store_true",
                        help="deep audit: re-check every due link (02:00 local)")
    parser.add_argument("--no-state", action="store_true",
                        help="do not read/write the per-URL cooldown state file")
    args = parser.parse_args(argv)

    import config as config_mod
    if not getattr(config_mod.Config, "SUPABASE_ENABLED", False):
        print("supplier watchdog: Supabase credentials are not configured; "
              "refusing to write stock to a throwaway local catalogue.",
              file=sys.stderr)
        return 2

    import supplier_watchdog

    state_used = False
    if not args.no_state:
        state_used = supplier_watchdog.load_state()

    try:
        if args.nightly:
            try:
                max_products = int(os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY_MAX", "400") or 400)
            except (TypeError, ValueError):
                max_products = 400
            try:
                links = int(os.environ.get("SUPPLIER_WATCHDOG_NIGHTLY_LINKS", "200") or 200)
            except (TypeError, ValueError):
                links = 200
            result = supplier_watchdog.nightly_sweep(
                max_products=max_products, min_interval_seconds=0, link_limit=links)
        else:
            try:
                batch = int(os.environ.get("SUPPLIER_WATCHDOG_BATCH", "4") or 4)
            except (TypeError, ValueError):
                batch = 4
            try:
                cooldown = int(os.environ.get("SUPPLIER_WATCHDOG_MIN_INTERVAL", "3600") or 3600)
            except (TypeError, ValueError):
                cooldown = 3600
            result = supplier_watchdog.tick(
                limit=batch, min_interval_seconds=cooldown, link_limit=2)
    except Exception as exc:                       # noqa: BLE001 - CLI boundary
        print(f"supplier watchdog crashed: {type(exc).__name__}: {str(exc)[:300]}",
              file=sys.stderr)
        return 2
    finally:
        if not args.no_state:
            supplier_watchdog.save_state()

    line = _summary_line(result)
    print(line)
    if state_used:
        print("supplier watchdog: restored the per-URL cooldown state", file=sys.stderr)
    print("SUPPLIER_SUMMARY=" + json.dumps(result, ensure_ascii=False), file=sys.stderr)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("### " + ("Nightly deep audit" if args.nightly else "Live update") + "\n\n")
                fh.write("```\n" + line + "\n```\n")
        except Exception:
            pass
    if result.get("lastError"):
        print("supplier watchdog: " + str(result["lastError"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
