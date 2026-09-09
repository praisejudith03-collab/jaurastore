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

