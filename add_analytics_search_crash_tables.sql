-- ===========================================================================
-- Jaura Store — analytics search history, lifetime counters and crash reports
-- ===========================================================================
--
-- Adds the three tables introduced with the geolocation / crash-tracing /
-- persistence work:
--
--   search_queries     what customers typed into the shop search box, so the
--                      demand signal survives a redeploy
--   analytics_counters the store's lifetime odometer — totals that must never
--                      restart at zero when a deploy replaces the disk
--   job_failures       background-worker crash reports (exception, stack
--                      trace, memory, payload id) so "background scheduler
--                      workers are not healthy" can be diagnosed
--
-- This file is the same SQL as the tail of schema_sections/17_analytics.sql,
-- extracted so it can be run on its own against a database that already has
-- the rest of the schema. It is:
--
--   * IDEMPOTENT — every statement is "if not exists"; running it twice is a
--     no-op and the second run reports success with no rows.
--   * NON-DESTRUCTIVE — it only CREATEs the three tables above and turns on
--     Row Level Security for them. Nothing is dropped, renamed, deleted or
--     backfilled, and no pre-existing table, row, policy, bucket or object is
--     touched. The only ALTERs are "enable row level security" on the three
--     tables this file just created.
--   * SAFE ON A LIVE SHOP — the app keeps working while it runs, and keeps
--     working if you never run it (the writes are best-effort mirrors; the
--     shop counts locally in SQLite either way).
--
-- Run it in the Supabase SQL Editor with the postgres role. Takes ~1 second.
-- Verification queries are at the bottom of this file.
-- ===========================================================================

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

-- Row Level Security. Supabase flags a new public table without RLS as
-- CRITICAL, and it is right to: these tables hold visitor paths, search terms
-- and stack traces that no browser key should ever reach.
--
-- The server reaches Supabase with SUPABASE_SERVICE_ROLE_KEY, which BYPASSES
-- RLS, so the app keeps reading and writing normally with no policy attached.
-- Enabling RLS with zero policies is a deny-all for anon and authenticated
-- clients and a no-op for us. That is why no "create policy" follows.
--
-- Safe to re-run: enabling RLS twice is not an error.
alter table search_queries     enable row level security;
alter table analytics_counters enable row level security;
alter table job_failures       enable row level security;


-- ===========================================================================
-- VERIFICATION (read-only — safe to run any time, changes nothing)
-- ===========================================================================
-- Run this in a NEW query after the statements above report success.
-- Expect exactly three rows: analytics_counters, job_failures, search_queries.
--
--   select table_name
--   from information_schema.tables
--   where table_schema = 'public'
--     and table_name in ('search_queries', 'analytics_counters', 'job_failures')
--   order by table_name;
--
-- Confirm Row Level Security is on (expect three rows, all true). If any is
-- false, Supabase's linter will flag it as CRITICAL:
--
--   select relname, relrowsecurity from pg_class
--   where relname in ('search_queries', 'analytics_counters', 'job_failures');
--
-- Confirm the indexes landed (expect four rows):
--
--   select indexname from pg_indexes
--   where schemaname = 'public'
--     and indexname in ('idx_search_queries_day', 'idx_search_queries_norm',
--                       'idx_job_failures_at', 'idx_job_failures_job')
--   order by indexname;
--
-- After the app has served some traffic, these should start filling up.
-- All three being empty immediately after the migration is normal and
-- expected — they are written as shoppers browse and search.
--
--   select count(*) from search_queries;
--   select name, value, updated_at from analytics_counters order by name;
--   select job, error_type, payload_id, rss_mb, at
--   from job_failures order by at desc limit 20;
--
-- An empty job_failures table is the GOOD outcome: it means no background
-- worker has crashed.
-- ===========================================================================
