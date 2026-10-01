# Supabase Storage cleanup

`tools/cleanup_supabase_storage.py` inventories every bucket and cross-references Storage URLs in `products`, `featured_products`, `categories`, `hero_banners`, `site_settings`, `orders`, and `receipts`.

It is deliberately **dry-run by default**:

```bash
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
  python3 tools/cleanup_supabase_storage.py --report storage-cleanup-report.json
```

Review `delete_candidates`, `duplicate_groups`, and `broken_videos` in the report. A referenced object is never a delete candidate. If any reference table exists but cannot be read, the run aborts without deleting anything. Tables that do not exist in this deployment are recorded as `not present`.

After review, apply to the exact project (the project-ref confirmation prevents targeting the wrong environment):

```bash
python3 tools/cleanup_supabase_storage.py --apply \
  --confirm-project YOUR_PROJECT_REF --report storage-cleanup-applied.json
```

Only unreferenced recognized media/document objects are removed. Unknown/system files are left untouched. Deletions are batched and the report records every removed `bucket/path`.

## Automatic nightly sweep

Since the nightly-maintenance release the scheduler also runs this cleanup
**automatically** once a day, after 2:00 AM in the owner's timezone (see
`SUPPLIER_WATCHDOG_NIGHTLY_*` in `ENVIRONMENT_VARIABLES.md`). The automatic
sweep uses the same protections as the manual flows:

* files referenced by a live product, order, receipt or the site are never
  candidates;
* uploads younger than two days are protected (they may still be mid-save);
* a failed reference scan aborts the sweep instead of guessing.

It is the same plan as the Admin Portal's **Settings → Advanced settings →
Storage cleanup** card and this CLI tool, so all three stay in agreement. To
keep the nightly supplier sweep but skip the automatic purge, set
`STORAGE_SWEEPER_NIGHTLY=0` in the environment.
