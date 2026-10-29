# Supplier email alerts, manual French titles & Section 21 — release notes

**Session:** 2026-10-29 · **Branch:** `arena/f94a7b13-jaurastore`
**Tests:** 2,556 passed, 41 skipped (optional browser/Postgres harness in
sandbox), 0 failed. Asset token bumped v228 → v229 (admin.js changed).

## 1. Supplier stock: alert-only + email to jaurastore@gmail.com

- The watchdog **never writes shop stock**. It records only the
  diagnostic `supplier_status` badge (as before) and now also queues an
  owner email on transitions. Shop `stock` / `optionStock` / online are
  never touched — pinned by
  `tests/test_supplier_stock_alerts.py::test_watchdog_never_writes_shop_stock_and_records_raw_readings`
  (any call to the stock writer fails the test).
- **Levels:** supplier 0 = OUT, 1–9 = LOW (`SUPPLIER_LOW_STOCK_THRESHOLD`,
  default 9), bare "In stock" (no count) = healthy, never alerts.
  This also fixed a live bug: a bare in-stock flag on simple products
  was being recorded as *out of stock*.
- **One email per transition** per product/variant: entering low/out,
  or worsening low → out. Staying low never re-emails; recovering to
  healthy re-arms the alert. State is durable in Supabase
  (`growth_settings.supplier_stock_alert_state_json`) — **no SQL
  migration needed** for alerts.
- **Sending:** the GitHub Actions watchdog queues alerts every 20 min;
  the Render scheduler's 5-minute tick emails the batch to
  `ADMIN_EMAIL` (jaurastore@gmail.com) using the same Resend/Brevo
  transport as order mail. If you also add `RESEND_API_KEY` +
  `MAIL_FROM` as GitHub repo secrets, Actions sends immediately too;
  a claim stamp prevents duplicates. Render env is already declared in
  `render.yaml` (`SUPPLIER_STOCK_ALERTS_ENABLED=1`).
- Email lists product, variant, supplier quantity left, your current
  shop stock, and links to **Admin → Supplier Alerts**, where you press
  the toggle / edit stock manually when free.

## 2. Manual French title box

- Admin product form (create + edit): **"Name (French) / Title
  (Français)"** (`nameFr`) directly under Product title. Save payload
  and per-field merge now carry it; the DB column, storefront and
  WhatsApp broadcast already read it. Owner wording always wins over
  the read-time French backfill tables.
- Two older editor-inventory tests updated with the reversal noted:
  nameFr was deliberately removed in 2026-10-19 on the assumption the
  seed shipped it for everything — false for Admin-created products.

## 3. Currency/language rule — verified, already as specified

French interface → `currencyLocked()` forces CFA everywhere and hides
the ₦/CFA pill; English → opens in NGN on every fresh visit, pill
switches to CFA for the session. Pinned by
`tests/test_auto_locale_rules.py`, `tests/test_naira_first_and_cart_pill.py`
and a new assertion in `tests/test_manual_french_title.py`. No code
change was needed. (Checkout payment *methods* stay available in both
languages; amounts follow the chosen method.)

## 4. Section 21 + Clothing purge

Owner pack: **`SUPABASE_SECTION_21_AND_CLOTHING_PURGE.md`** — exact
paste-ready SQL (verified byte-identical to
`schema_sections/21_hard_delete_categories.sql`), pre-flight checks,
product reassignment, Admin delete, tombstone verification.
`verify_schema.py` now also probes `deleted_categories` (17 required
tables) so a missing Section 21 shows up in verification, not at
delete-time.

## Owner next steps

1. Merge this PR → Render deploys from `main`; the first scheduler tick
   starts draining any queued supplier alerts.
2. Admin → Orders → "Email a test" once, to confirm, mail transport.
3. Run the Section 21 pack in Supabase, then delete **Clothing**.
4. Optional: add `RESEND_API_KEY`/`MAIL_FROM` GitHub secrets for
   immediate (vs ≤5-minute) alert emails.
5. Backfill French titles for perfumes/new products via the new box.
