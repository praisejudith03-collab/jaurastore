# Migration: search history, lifetime counters and crash reports

**What this adds:** three Supabase tables that back the geolocation /
crash-tracing / persistence work.

| table | what it holds | what breaks without it |
| --- | --- | --- |
| `search_queries` | what customers typed into the shop search box | search history resets on every deploy |
| `analytics_counters` | the store's lifetime odometer (page views, events, searches) | headline totals restart at zero on every deploy |
| `job_failures` | background-worker crash reports — exception, stack trace, memory, payload id | a worker crash leaves no evidence once the dyno recycles |

**Time:** about one minute. **Downtime:** none — the shop keeps serving and
keeps counting locally in SQLite throughout.

---

## Before you start

This migration is **additive only**. Every `create` is `if not exists`; the
file contains no `DROP`, `DELETE`, `TRUNCATE`, `UPDATE` or `INSERT` (a test
enforces that). The only `ALTER`s enable Row Level Security on the three
tables the same file just created — a test pins them to exactly that, so an
`ALTER` against a pre-existing table cannot slip in. Nothing existing is
dropped, renamed, altered, backfilled or deleted. Running it twice is a no-op.

**On Row Level Security:** these three tables hold visitor paths, search terms
and stack traces, so no browser key should reach them. The app connects with
`SUPABASE_SERVICE_ROLE_KEY`, which *bypasses* RLS, so turning RLS on with no
policy attached is a deny-all for anon/authenticated clients and a no-op for
the app. That is why the file enables RLS and attaches no `create policy`.

You do **not** need to:

- re-run the other schema sections (01–18) — they are untouched;
- redeploy the app first — the app works with or without these tables;
- stop the shop, pause the workers or put the site in maintenance.

If you skip this migration entirely the shop still works: the mirror writes
are best-effort, so analytics and searches are counted in SQLite and simply
lose their off-box durability. You would keep the "resets on deploy" symptom,
which is the thing this fixes.

---

## Step 1 — open the Supabase SQL Editor

1. Go to **https://supabase.com/dashboard** and sign in.
2. Pick the correct organization, then the **Jaurastore** project. Check the
   project reference in the URL matches the host in your configured
   `SUPABASE_URL` — this is the one step worth double-checking, because
   running it against the wrong project creates three stray empty tables.
3. Open **☰ → SQL Editor → New query**. Use the **Primary Database** with the
   **postgres** role.

Do not paste any API key, service-role key or password into a query.

## Step 2 — run the SQL

Open `add_analytics_search_crash_tables.sql` from the repository root, tap
**Raw**, select all, copy, and paste the whole file into the empty editor.
Or paste this directly — it is the same SQL:

```sql
-- What customers typed into the shop search box, mirrored from SQLite so
-- the demand signal survives a redeploy like every other insight.
create table if not exists search_queries (
  id       bigserial primary key,
  vid      text,
  sid      text,
  q        text not null,
  q_norm   text not null,
  results  integer not null default 0,
  category text,
  city     text,
  country  text,
  day      text not null,
  at       timestamptz not null default now()
);
create index if not exists idx_search_queries_day on search_queries(day);
create index if not exists idx_search_queries_norm on search_queries(q_norm);

-- Lifetime counters (page views, searches, ...) that must never reset to
-- zero on a deploy. One row per counter; the app keeps the maximum of the
-- local and remote value, so neither side can roll the odometer back.
create table if not exists analytics_counters (
  name       text primary key,
  value      bigint not null default 0,
  updated_at timestamptz not null default now()
);

-- Background-job crash reports: scheduler ticks and notification sends that
-- raised, with the traceback, the memory the worker was using and the
-- payload id that failed. Written by observability.record_failure().
create table if not exists job_failures (
  id         bigserial primary key,
  job        text not null,
  worker     text,
  payload_id text,
  error_type text,
  message    text,
  traceback  text,
  rss_mb     numeric,
  attempt    integer not null default 1,
  host       text,
  at         timestamptz not null default now()
);
create index if not exists idx_job_failures_at on job_failures(at desc);
create index if not exists idx_job_failures_job on job_failures(job);

-- Deny-all for browser keys; the server's service-role key bypasses RLS.
alter table search_queries     enable row level security;
alter table analytics_counters enable row level security;
alter table job_failures       enable row level security;
```

Tap **Run**. Expect **Success. No rows returned**.

If Supabase interrupts with **"Potential issue detected — this query creates
tables without enabling Row Level Security"**, the SQL above already handles
it on the last three lines, so either button works. Prefer **Run and enable
RLS**; if you tap **Run without RLS**, the `alter table` statements still
switch it on.

If you get an error, stop and record it rather than working around it by
dropping anything. The likely causes are being on the wrong project, or the
`analytics_events` table from the earlier durable-insights work never having
been applied (section 17) — that one is separate and this file does not
create it.

## Step 3 — verify (read-only)

In a **new** query:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('search_queries', 'analytics_counters', 'job_failures')
order by table_name;
```

Expect exactly three rows: `analytics_counters`, `job_failures`,
`search_queries`. Then confirm Row Level Security is on — all three must come
back `true`, otherwise Supabase's linter (the lightbulb icon) flags them
CRITICAL:

```sql
select relname, relrowsecurity from pg_class
where relname in ('search_queries', 'analytics_counters', 'job_failures');
```

Then confirm the indexes (expect four rows):

```sql
select indexname from pg_indexes
where schemaname = 'public'
  and indexname in ('idx_search_queries_day', 'idx_search_queries_norm',
                    'idx_job_failures_at', 'idx_job_failures_job')
order by indexname;
```

All three tables being **empty at this point is correct** — they fill as
shoppers browse and search.

## Step 4 — deploy the application

Merge the branch and let Render deploy both services, or trigger a manual
deploy. No new environment variable is required.

Two optional ones, if you want them (Render → Environment):

- `ANALYTICS_BOT_IP_NETWORKS` — extra crawler/datacentre CIDR ranges to keep
  out of analytics, comma or space separated. The built-in list already
  covers Googlebot, Bing, Hetzner Helsinki, DigitalOcean, Linode and OVH.
- `GITHUB_TOKEN` + `GITHUB_REPOSITORY` — already set if repo sync is on.
  With them, each worker crash also opens/comments on a GitHub issue via
  `.github/workflows/crash-report.yml`.

## Step 5 — confirm it is working end to end

**a. The workers are healthy and reporting detail.** Open
`https://jaurastore.com.ng/healthz`. The `background` block should show
`"started": true`, `"maintenanceAlive": true`, `"remindersAlive": true`, and
a `recentFailures` list (empty is the good outcome).

**b. Locations are real.** Admin Portal → **Dashboard** → *Visitor locations*.
New entries should be the countries you actually sell to. Finland and other
datacentre entries stop appearing; existing rows from before the fix stay in
the table until they age out of the retention window — they are historical
data, not a live fault.

**c. Searches are recorded.** Search for something on the storefront, then
Admin Portal → **Dashboard** → *What customers searched for*. Or check
directly:

```sql
select q, results, country, at from search_queries order by at desc limit 10;
```

**d. The odometer survives a deploy.** Note the "since launch" figure under
*Page views* on the dashboard, trigger a redeploy, and check it again — it
must not drop. In SQL:

```sql
select name, value, updated_at from analytics_counters order by name;
```

**e. Crash reports land.** Admin Portal → **Orders** → *Background job
failures* — ten per page, each row collapsible to reveal the stack trace,
memory and payload id. An empty list is the good outcome. To confirm the
pipeline works rather than waiting for a real crash, watch this table after
the next deploy; transient Supabase or mail-provider blips show up here
first.

## Rollback

Nothing to roll back — the migration only creates tables. If you want to undo
it, the app tolerates the tables being absent (mirror writes fail
best-effort), so you can drop them with no effect on orders, products,
customers or receipts:

```sql
drop table if exists search_queries;
drop table if exists analytics_counters;
drop table if exists job_failures;
```

Only do this if you deliberately want to give up the durability — dropping
`analytics_counters` restores the "totals reset on deploy" behaviour.

---

## Local / SQLite deployments

No action needed. `init_db()` runs the full `CREATE TABLE IF NOT EXISTS`
script on every boot, so an existing SQLite database picks up all three
tables automatically on the next restart.

## Pre-flight check

`verify_schema.py` now probes the new tables, so you can confirm the whole
schema from a machine with the credentials set:

```sh
python3 verify_schema.py            # live probe against Supabase
python3 verify_schema.py --dry-run  # no network; checks the SQL file only
```

It prints table and column names only, never the service-role key.
