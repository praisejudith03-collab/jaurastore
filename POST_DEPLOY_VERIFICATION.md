# Post-deploy verification

The 2026-10-20 release ended with "verify and deploy". The local suite proves
the code; these two checks prove the **deployed** service and the **admin
toast** the owner actually sees.

## 1. Verify a live deploy

```bash
python3 tools/post_deploy_check.py                    # https://jaurastore.com.ng
python3 tools/post_deploy_check.py --json             # machine-readable
python3 tools/post_deploy_check.py --checks healthz,catalog
python3 tools/post_deploy_check.py --base https://staging.example --samples 9
```

Read-only: no credentials, no writes, nothing cached. Exit `0` when every
selected check passes, `1` when one failed, `2` when nothing answered at all
(DNS/TLS/connection).

| Check | What it proves | Fails on |
| --- | --- | --- |
| `healthz` | `GET /healthz` answers `200 ok`, and the server's own `latencyMs` is inside the **50 ms** budget for ≥ 80 % of samples | a slow or unhealthy probe, wall time over 2 s |
| `catalog` | `meta.publishedCount == len(products)`, `count ≥ publishedCount`, `hiddenCount == count − publishedCount`, no duplicate or id-less row | **a row the API counted but did not serve** — the invariant behind "I saved a product and the shop does not show it" |
| `site` | `GET /api/site` serves a live rate (`cfaRate > 0`) | a zero rate, which is what made every Naira price read 0 in CFA |
| `shop` | `GET /shop.html` with `Accept-Language: fr` is stamped `lang="fr"`, all `?v=` tokens match, the grid container exists | English first paint, a half-bumped build, a missing grid |
| `audit` | `GET /api/admin/catalog/audit` turns an anonymous caller away — 401/403, or a redirect to the login page | a 200 (public audit: a leak) or a 404 (the deploy did not land) |

Output against a local gunicorn (1 worker, 4 threads, the Render shape):

```
PASS  healthz  server p50 0.04 ms, max 0.88 ms, 100% under 50 ms; wall p95 2 ms
PASS  catalog  257 published of 257 live (0 hidden), no duplicates, 157 KB in 55 ms
PASS  site     cfaRate 0.44
PASS  shop     lang="fr" before JS, asset token v219, grid present
PASS  audit    admin-only (HTTP 401)

POST-DEPLOY CHECKS PASSED  (80 ms, http://127.0.0.1:8059)
```

Every line is a sentence an operator can act on, not an assertion dump. When a
check fails the run stops at the first problem it can describe precisely, e.g.

```
FAIL  catalog  publishedCount=257 but 254 products were served - 3 row(s) were counted and not served (the "saved product is missing" signature)
FAIL  site     HTTP 503, Site settings are temporarily unavailable.
FAIL  shop     lang=en for a French device (the French first paint regressed)
FAIL  audit    the catalogue audit answers 200 without a session - it leaks the catalogue
```

The `catalog` line is the one that matches the owner's report
("I saved a product and it is not in the shop"): it fires when the API counts
a row it does not serve. Triage on the same deployment:
`GET /api/admin/catalog/audit` (admin session) names every missing id and why —
`missingFromPublic` empty means the API served everything, and the product is
then `hidden`, uncategorised (`noCategory`), unpriced (`noPrice`) or on the
branded placeholder photo.

`--json` prints the same verdicts as
`{ok, base, checkedAt, elapsedMs, checks:[{check, ok, detail}]}` for a CI step
or a cron watchdog.

**Network note:** the verifier talks to the origin directly. Run it from a
machine with normal internet access (or a Render shell); a sandbox whose egress
allowlist blocks the site reports every check as `request failed: ...` and
exits `2` — that is "could not verify", deliberately distinct from "failed".

## 2. Verify the admin toast in a real DOM

```bash
JA_JSDOM_DIR=/tmp/ja-jsdom node tests/_toast_check.mjs
JA_JSDOM_DIR=/tmp/ja-jsdom python3 -m pytest tests/test_admin_toast_dom.py -q
```

The check loads the shipped `js/store.js` in jsdom and asserts what the admin
sees after a save: exactly one `.toast`, `data-kind="success"`, the green
`is-success` class, the exact sentence, the same element reused (never a second
toast) for the next notice, `data-kind="error"`/`is-error` — never green — for
a failure, and the element hiding itself when the 2.2 s timer expires. It also
checks that `css/style.css` still carries both variants.

The browser-based checks (Playwright) remain opt-in: they need chromium, which
CI installs, and `JA_REQUIRE_BROWSER=1` turns a missing browser into a failure
instead of a skip.

## 3. Why these live outside the app

Neither tool is imported by the running service: the verifier is a plain
`urllib` script (no Flask, no Supabase) and the toast check is a Node harness.
Nothing here can slow down or break a request, and both can run from a
developer laptop, from CI, or from a Render shell after a deploy.
