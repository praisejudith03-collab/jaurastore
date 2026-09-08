-- SECTION: product_reviews
-- Product reviews. Mirrors the SQLite product_reviews table one-for-one so
-- the same shape can be read from either side. unique(product_id, email) is
-- what enforces one review per customer per product - the same rule the
-- purchase-verified check relies on.
-- Product reviews. unique(product_id, email) is what enforces one review per
-- customer per product - the same rule the purchase-verified check relies on.
--
-- Column names are the agreed contract: rating / title / body / created_at /
-- updated_at. An earlier draft shipped this table as stars / note / at; the
-- guarded renames below move any database that already has it, preserving
-- every row. A column rename rewrites no data and drops no row.
create table if not exists product_reviews (
  id          bigint generated always as identity primary key,
  product_id  text not null,
  order_id    text,
  email       text not null,
  name        text,
  rating      integer not null default 5 check (rating between 1 and 5),
  title       text,
  body        text,
  hidden      boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (product_id, email)
);

-- Idempotent upgrade path for a database created from the earlier draft.
-- Each step is guarded so re-running the file changes nothing.
do $$
begin
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'stars') then
    alter table product_reviews rename column stars to rating;
  end if;
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'note') then
    alter table product_reviews rename column note to body;
  end if;
  if exists (select 1 from information_schema.columns
             where table_name = 'product_reviews' and column_name = 'at') then
    alter table product_reviews rename column at to created_at;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'title') then
    alter table product_reviews add column title text;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'hidden') then
    alter table product_reviews add column hidden boolean not null default false;
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'created_at') then
    alter table product_reviews
      add column created_at timestamptz not null default now();
  end if;
  if not exists (select 1 from information_schema.columns
                 where table_name = 'product_reviews' and column_name = 'updated_at') then
    alter table product_reviews
      add column updated_at timestamptz not null default now();
  end if;
end $$;

create index if not exists product_reviews_pid on product_reviews(product_id);

