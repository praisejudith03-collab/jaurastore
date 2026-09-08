-- SECTION: admin_credentials
-- Admin credentials. The SQLite `admins` table lives on the Render disk, which
-- is EPHEMERAL: after a redeploy it is re-seeded with a deliberately unusable
-- random hash, which locked every admin out until someone got shell access.
-- This table is the durable copy of the password hash, so a restart restores
-- it instead of destroying it. Only a werkzeug hash is ever stored - never a
-- plaintext password, and never anything derived from SECRET_KEY.
create table if not exists admin_users (
  id            bigint generated always as identity primary key,
  email         text not null unique,
  password_hash text not null,
  role          text not null default 'admin',
  enabled       boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  last_login_at timestamptz
);
-- Repair an older admin_users table. Add-only, preserves existing admins.
alter table admin_users add column if not exists email         text;
alter table admin_users add column if not exists password_hash text;
alter table admin_users add column if not exists role          text not null default 'admin';
alter table admin_users add column if not exists enabled       boolean not null default true;
alter table admin_users add column if not exists created_at    timestamptz not null default now();
alter table admin_users add column if not exists updated_at    timestamptz not null default now();
alter table admin_users add column if not exists last_login_at timestamptz;

create table if not exists admin_reset_tokens (
  id bigint generated always as identity primary key,
  email text not null,
  purpose text not null default 'reset',
  token_hash text not null,
  expires_at timestamptz not null,
  attempts integer not null default 0 check (attempts >= 0),
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);
-- Repair an older admin_reset_tokens table. Add-only, before the index.
alter table admin_reset_tokens add column if not exists email       text;
alter table admin_reset_tokens add column if not exists purpose     text not null default 'reset';
alter table admin_reset_tokens add column if not exists token_hash  text;
alter table admin_reset_tokens add column if not exists expires_at  timestamptz;
alter table admin_reset_tokens add column if not exists attempts    integer not null default 0;
alter table admin_reset_tokens add column if not exists consumed_at timestamptz;
alter table admin_reset_tokens add column if not exists created_at  timestamptz not null default now();
create index if not exists admin_reset_tokens_lookup on admin_reset_tokens(email, purpose, created_at desc);

