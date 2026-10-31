# is_deleted migration + Supplier Alerts scope

Two changes that ship together.

## 1. `public.products.is_deleted` (Trash Bin flag)

Migration: `migrations/20261031_products_is_deleted.sql`

```sql
ALTER TABLE public.products ADD COLUMN IF NOT EXISTS is_deleted boolean DEFAULT false;
```

- Run it once in the Supabase SQL editor, with **Run without RLS**.
- Idempotent. It adds the column only and updates no rows.
- `schema_sections/01_products.sql` and `auto_migrate.py` define the same column as
  `NOT NULL`. If either has already run, this statement is a no-op.

**Save handler.** Before this change, a products table without `is_deleted` refused
every product save with "The product could not be saved". The save path now:

- retries without `is_deleted` when the column is absent **and** every row carries
  it as `false` (or not at all). Dropping a `false` loses nothing, because without
  the column no tombstone can exist. The save goes through and a warning
  (`products_table_optional_columns_missing`) is recorded once per process;
- still **refuses** a row that carries `is_deleted = true` on a table without the
  column, so a trashed piece is never silently revived;
- still refuses a save when any other product column is missing (unchanged).
- handles both PostgREST (`Could not find the 'is_deleted' column`) and raw
  Postgres (`column "is_deleted" does not exist`) error text.

## 2. Supplier Alerts: live and in-stock items only

**Dashboard (Admin → Supplier Stock Alerts).** The list now shows only products that
are:

- active on the website (not hidden, not trashed, not soft-deleted), and
- in stock (`stock > 0`; for a variant, its own option stock must be `> 0`).

A supplier low/out flag on an item the owner has already hidden, trashed or set to
zero is left out. The summary shows these as **Left out** (`counts.excluded`).

**Emails.** Only supplier stock changes on active, in-stock items are queued:

- the watchdog re-reads the live listing before queueing (its candidate could be
  minutes old), and skips readings for items the shop is not selling;
- a pending alert whose item has since become inactive or sold out is closed
  **without** an email (`handledAt` is set, `emailedLevel` is recorded so the same
  situation is not re-queued);
- if the catalogue cannot be read, nothing is sent and the alerts stay pending.

**Unchanged.** The watchdog still never writes shop stock. Restocking remains a
manual action.

## Operator checklist

1. Run `migrations/20261031_products_is_deleted.sql` in Supabase (optional, but it
   removes the warning).
2. Deploy. Check Admin → Supplier Stock Alerts: the list should contain only live,
   in-stock items, and "Left out" should count the rest.
3. No env var changes.

## Mark as seen, and sorting (follow-up)

- **Mark as seen.** Each supplier alert has a "Mark as seen" button, on the
  Supplier Alerts page and in the dashboard's supplier drawers. It only changes
  the owner's view. It never changes the shop and never sends an email.
  Endpoint: `POST /api/admin/supplier-alerts/<product id>/mark-seen` (CSRF).
- **New vs seen.** The Supplier Alerts page shows "New / Unseen Alerts" at the
  top and a collapsible "Previously Seen" section at the bottom. The dashboard's
  supplier queues show only unseen drops.
- **When an alert counts as new again.** A new drop, or an escalation (low to
  out), is unseen. Easing (out to low) stays seen. Going back up after easing
  is a new drop. Recovering to ok clears the alert.
- **Existing alerts.** Ledger entries written before this release are treated
  as already seen, so the first deploy does not flood the New list.
- **Sorting.** `?sort=urgent` (default: out first, then by name), `newest`
  (most recent change first), or `name`.
- **Unreadable ledger.** If the alert record cannot be read, every alert is
  shown as new, with a note. "Mark as seen" is refused with an error, so nothing
  is hidden without being recorded.
- Ledger entries gain `seenAt`, `seenLevel` and `changedAt`. No product rows
  are written by any of this.
