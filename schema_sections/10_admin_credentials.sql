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
create index if not exists admin_reset_tokens_lookup on admin_reset_tokens(email, purpose, created_at desc);

