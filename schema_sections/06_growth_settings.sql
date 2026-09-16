-- SECTION: growth_settings
-- ----------------------------------------------------- growth settings
-- key/value map: referralEnabled, minSpendNgn, cfaRate,
-- buyerPercent, referrerPercent, milestone
create table if not exists growth_settings (
  key   text primary key,
  value text
);
-- Repair an older growth_settings table that may be missing the value column.
alter table growth_settings add column if not exists value text;

-- Campaign audit log. Recipient addresses are intentionally not stored here;
-- the count is enough for reporting without duplicating customer contact data.
create table if not exists marketing_campaigns (
  id                text primary key,
  campaign_type     text not null,
  subject           text not null,
  content           text not null,
  recipient_count   integer not null default 0,
  sent_count        integer not null default 0,
  failed_count      integer not null default 0,
  status            text not null default 'sent',
  sent_at           timestamptz not null,
  created_at        timestamptz not null default now()
);
create index if not exists idx_campaigns_sent_at on marketing_campaigns (sent_at desc);

