# Storefront publish diagnosis — "it saves in the portal, it is not in the shop"

**Date:** 2026-10-31 · **Scope:** the admin-save → public-catalogue pipeline ·
**Status:** four defects found and fixed, regressions pinned by
`tests/test_new_product_publish.py`, `tests/test_admin_save_transport.py`;
full suite green (2 580 passed, 13 skipped).

---

## 0. What the live site actually showed

Read-only probes of `https://jaurastore.com.ng` on 2026-10-31:

| Probe | Result |
|---|---|
| `GET /api/catalog` | `count: 281`, `publishedCount: 281`, `hiddenCount: 0`, every row `online: true` |
| `GET /api/health` | `ok: true`, `env: production`, Supabase round trip ≈ 433 ms, background/maintenance/supplier threads alive, stock guardrail 281 checked / 0 disagreements |
| `GET /api/stock` (derived from the same `merged()` catalogue the shop renders) | ids ascend `jau-mtot…` → `jau-muwvc3vt` (created **2026-10-06T16:03Z**) → `wix-001` … `wix-257` |
| `GET /shop.html` | renders "281 products", newest-first |

**The decisive fact:** product ids encode their creation time
(`jau-` + base36(epoch ms)), so the catalogue itself is a creation log. The
newest id on the live shop is from **06 October**. Nothing created on 30 or 31
October exists in the table — and no row is hidden (`hiddenCount: 0`), so the
catalogue is not filtering them out either.

That rules out every "the shop is hiding it" theory (query filters, drafts,
`online = false`, pagination, ordering, service-worker caching) and points at
the **write**: the products the owner added yesterday and today never reached
the database. The portal showed them anyway, because the editor paints the
product into the local catalogue *before* the server confirms it — and the
transport made that unconfirmed state quiet.

Everything below is about making that impossible: a save either publishes, or
it says exactly why it did not.

---

## 1. Defect 1 — every admin save was capped at five seconds

`js/net.js` clamped **every** JSON request to 5 s, and a caller could not ask
for more:

```js
timeout: Math.min(5000, Math.max(1, Number(opts.timeout) || 5000))
```

A product save is not a five-second operation on this deployment: it performs a
cover check, a PostgREST write, a read-back, the storage sweep and an audit
write, each round trip ≈ 430 ms (see `/api/health`), on a free Frankfurt
instance reached over Nigerian mobile data. A save that needed eight seconds
was aborted at five, reported as *"Product is taking too long. It is safely
queued to retry"*, and re-sent from scratch — up to eight times, then dropped.

**Fixed:** the 5 s default stays (a catalogue read, an order), but a caller may
ask for more, up to a 120 s ceiling. `js/store.js` now saves products with a
**30 s** deadline (just under gunicorn's 45 s worker timeout), for the product
save and for the permanent delete.

Pinned by: `tests/_net_save_check.mjs` case 1 (a 5.6 s save with
`timeout: 45000` must succeed, not be queued).

---

## 2. Defect 2 — a server refusal was swallowed as "queued"

When the server **answered** and refused (503/429), `js/net.js` put the call in
the outbox and returned `{queued: true}` — with the server's reason thrown
away. `js/store.js` then returned `{ok: false, queued: true, silent: true}`, so
the editor printed only *"Product save is queued but not yet confirmed"*. The
exact reason the API had already produced
(`"the products table is missing the column 'supplierCost'; run
supabase_schema.sql"`, `"cover: relative upload path is not verified…"`) never
reached the person who could fix it, while the optimistic local copy kept the
product visible in the portal.

**Fixed:** a refusal that came from the server now travels back through
`api()` → `upsertProduct()` → the editor, which prints
*"Product save is queued but not confirmed — it is NOT on the shop yet. The
server said: …"*. A dropped connection stays quiet (there is no reason to
print, and the durable outbox retries it).

Pinned by: `tests/_net_save_check.mjs` cases 2 and 3.

---

## 3. Defect 3 — the newer copy of a product could lose to the older one

`catalog._dedupe_products` folds two rows into one card when they share a name
**and** a slug or sku (a re-created piece). It kept the row it met **first** —
the older one. Two second-resolution timestamps made this worse:
`supabase_store._now()` wrote `updated_at` with whole seconds, so two saves in
the same second were indistinguishable.

Consequence: the owner saves a product, cannot see it, saves it again — and the
**second** save is the copy the catalogue throws away. Both saves answered
`created`, the older row stayed on the shop forever, and the row the owner had
just written never appeared.

**Fixed:** the freshest row now wins (a row carrying a real `updated_at`
always beats one that carries none, e.g. the bundled seed), and `_now()` writes
microseconds so "newest" is never a tie. The fold still keeps exactly one card
— `tests/test_dedupe_scope.py` and the new
`test_readding_a_product_keeps_the_new_copy_not_the_stale_one` both pass.

Also fixed alongside it: a create allocates its slug against the catalogue
read cache, which is a few seconds old. Two products added in the same minute
could be handed the **same** slug — and then folded away by the same dedupe.
The cache is now dropped before a create computes its slug
(`tests/test_new_product_publish.py::test_two_quick_creates_with_the_same_name_both_publish`).

---

## 4. Defect 4 — the portal said "Live on the store now" without checking

`POST /api/admin/products` answered `ok: true` for any confirmed **write**. It
never asked whether the *catalogue build* still carried the row. A product can
be written and then be dropped one stage later: a durable tombstone left by an
earlier delete, a soft-delete flag another writer set, a dead `source`, the
fixture guard, or a dedupe fold. The owner then reads "Live on the store now"
next to a product no shopper can find.

**Fixed:** every confirmed save now runs `catalog.publication_status()` — the
storefront's own predicate — and answers with:

```json
{"ok": true, "product": {...}, "live": true}
{"ok": true, "live": false, "publishStage": "hidden",
 "publishReason": "the product is hidden (online = false)"}
```

`stages`: `hard-tombstone`, `legacy-tombstone`, `flagged`, `hidden`,
`fixture`, `permanently-removed`, `missing-row`, `dedupe`.

The one case a save may legitimately undo — the id still sitting on the legacy
durable delete list from an earlier delete — is **cleared and re-checked**
inside the save, so re-creating a piece heals its own tombstone. Every
`live: false` answer is written to the admin audit log as `product.not_live`.

The editor now prints *"Saved, but the shop is NOT showing it yet: <reason>"*
instead of the old unconditional success line. A product the owner deliberately
hid still says *"Hidden from the store"*.

`GET /api/admin/catalog/audit` also gained **`notPublished`**: every row the
products table holds that the published answer does not carry, each with its
reason — the list this whole report had to be reconstructed from.

---

## 5. What the owner should do after this deploy

1. Open **Admin → Products** and re-save the pieces added on 30–31 October.
   They are not in the database (see §0), so no code change can make them
   appear on their own.
2. Watch the toast. It now says one of:
   * *"Saved successfully · Live on the store now"* — done;
   * *"Saved, but the shop is NOT showing it yet: <reason>"* — the reason is
     the fix;
   * *"Product save is queued but not confirmed — it is NOT on the shop yet.
     The server said: …"* — the server refused the write and named why.
3. If the reason mentions a missing **column**, run `supabase_schema.sql` in
   the Supabase SQL editor (the same instruction `/api/health` and the
   diagnostics already give).

Verify from any browser:

```
GET https://jaurastore.com.ng/api/catalog          -> meta.count grows by 1
GET https://jaurastore.com.ng/api/admin/catalog/audit   (signed in)
    -> counts.publishedRows == counts.onlineRows, notPublished: []
```

---

## 6. Files touched

| File | Change |
|---|---|
| `js/net.js` | callers may request >5 s (120 s ceiling); a server-answered refusal is queued **with its reason** |
| `js/store.js` | product save + permanent delete use a 30 s deadline; the queued result carries the server message |
| `js/admin.js` | "Saved, but the shop is NOT showing it: …" / queued message now prints the server's reason; deliberate hide still reads "Hidden from the store" |
| `catalog.py` | `publication_status()`; dedupe keeps the freshest copy; a create refreshes the read cache before allocating its slug |
| `api.py` | every confirmed save reports `live` / `publishStage` / `publishReason` (audited); `/api/admin/catalog/audit` reports `notPublished`; legacy-tombstone self-heal |
| `supabase_store.py` | `updated_at` written with microsecond precision |
| `tests/test_new_product_publish.py` | new — one product, production write path, must be in `/api/catalog` |
| `tests/test_admin_save_transport.py`, `tests/_net_save_check.mjs` | new — the three transport rules an admin save depends on |
