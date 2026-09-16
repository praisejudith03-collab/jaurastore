-- SECTION: analytics
-- ------------------------------------------------------------ analytics
-- Durable store insights. Page views and engagement events used to live
-- only in the SQLite file on the Render disk, so every redeploy wiped the
-- dashboard. Each row is mirrored here (see supabase_store.
-- mirror_analytics_events) and create_app() copies the retention window
-- back into the local tables on boot (see analytics.restore_from_supabase).
-- Presence heartbeats stay local-only: they describe this dyno minute.
-- kind is 'page_view' for page_views rows, otherwise the events type
-- ('view', 'cart', 'checkout_start', 'purchase').
create table if not exists analytics_events (
  id           bigserial primary key,
  kind         text not null,
  vid          text,
  sid          text,
  path         text,
  page         text,
  ref          text,
  product_id   text,
  product_name text,
  value        numeric,
  currency     text,
  city         text,
  region       text,
  country      text,
  day          text not null,
  at           timestamptz not null default now()
);
create index if not exists idx_analytics_events_day on analytics_events(day);
create index if not exists idx_analytics_events_at on analytics_events(at desc);
create index if not exists idx_analytics_events_kind_day on analytics_events(kind, day);

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

-- Row Level Security. The server reaches Supabase with
-- SUPABASE_SERVICE_ROLE_KEY, which BYPASSES RLS, so the app reads and writes
-- these tables normally with no policy attached. Enabling RLS with zero
-- policies is therefore a deny-all for anon and authenticated clients and a
-- no-op for us -- which is what we want, because these tables hold visitor
-- paths, search terms and stack traces that no browser key should reach.
--
-- Deliberately different from site_settings (section 07), which keeps RLS off
-- because it predates this and is documented there. Do NOT copy that reasoning
-- to new tables.
--
-- These statements are idempotent: re-enabling is not an error. Verify with
--   select relname, relrowsecurity from pg_class
--   where relname in ('analytics_events', 'search_queries',
--                     'analytics_counters', 'job_failures');
-- All four must be true.
alter table analytics_events   enable row level security;
alter table search_queries     enable row level security;
alter table analytics_counters enable row level security;
alter table job_failures       enable row level security;
