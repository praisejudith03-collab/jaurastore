-- SECTION: customer_accounts
create table if not exists customers (
  id text primary key,
  email text not null unique,
  password_hash text not null,
  name text,
  phone text,
  country text,
  city text,
  delivery_address text,
  preferred_currency text default 'NGN',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table customers add column if not exists email text;
alter table customers add column if not exists password_hash text;
alter table customers add column if not exists name text;
alter table customers add column if not exists phone text;
alter table customers add column if not exists country text;
alter table customers add column if not exists city text;
alter table customers add column if not exists delivery_address text;
alter table customers add column if not exists preferred_currency text default 'NGN';
alter table customers add column if not exists created_at timestamptz not null default now();
alter table customers add column if not exists updated_at timestamptz not null default now();
create index if not exists idx_customers_email on customers(email);
