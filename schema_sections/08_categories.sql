-- SECTION: categories
create table if not exists categories (
  id text primary key,
  name text not null,
  name_fr text not null default '',
  image_url text not null default '',
  hidden boolean not null default false,
  updated_at timestamptz not null default now()
);
-- Repair an older categories table. Add-only, preserves all existing rows.
alter table categories add column if not exists name       text;
alter table categories add column if not exists name_fr    text not null default '';
alter table categories add column if not exists image_url  text not null default '';
alter table categories add column if not exists hidden     boolean not null default false;
alter table categories add column if not exists updated_at timestamptz not null default now();

