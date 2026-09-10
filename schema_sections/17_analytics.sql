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
