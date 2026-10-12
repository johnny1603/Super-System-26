-- Client credentials + billing-exempt accounts (core/client_auth.py).
-- Idempotent: safe to run more than once.
--
-- WHAT BREAKS UNTIL THIS RUNS (nothing that works today):
--   - Password sign-in, account setup, forgot-password and profile password
--     changes return ERR_AUTH_NOT_READY (503) and raise ONE admin alert per hour.
--   - Google sign-in redirects to /login/?google_error=unavailable.
--   - Admin "New Client" refuses to create the account (it will not create an
--     internal account without the billing_exempt flag - that would show up
--     in revenue reporting as a paying client at 0).
--   - Admin password reset fails.
-- Code login (emailed 6-digit code) keeps working throughout, and existing
-- session cookies stay valid (a missing session_version reads as 0).

alter table clients add column if not exists password_hash text;
alter table clients add column if not exists password_set_at timestamptz;
alter table clients add column if not exists email_verified_at timestamptz;
alter table clients add column if not exists google_sub text;
alter table clients add column if not exists failed_password_attempts integer not null default 0;
alter table clients add column if not exists password_locked_until timestamptz;
alter table clients add column if not exists session_version integer not null default 0;
alter table clients add column if not exists billing_exempt boolean not null default false;

-- One Google identity can sign in to at most one client
create unique index if not exists clients_google_sub_key
  on clients (google_sub) where google_sub is not null;
