-- Encrypted per-client credentials (core/secrets_vault.py). Idempotent.
--
-- Adds the columns only. The DATA migration (encrypting existing rows) is
-- scripts/encrypt_client_secrets.py — it needs SECRETS_ENCRYPTION_KEY, which
-- must never be in the DB, so it cannot be SQL.
--
-- RUN THIS BEFORE DEPLOYING the Phase 2A code: the new code writes these
-- columns on every connect. What breaks if it's missing: connecting or
-- re-connecting any tool fails (the insert/update is rejected) — nothing is
-- stored in plaintext instead.
--
-- The plaintext column `access_token` is NOT dropped here, on purpose: it is
-- the rollback path. It is blanked row by row only by the separate, confirmed
-- step `scripts/encrypt_client_secrets.py --drop-plaintext --confirm`.

alter table client_accounts add column if not exists access_token_enc text;
alter table client_accounts add column if not exists secret_last4 text;
alter table client_accounts add column if not exists secret_fingerprint text;

-- use_vendor_key: "does another client hold this same key?"
create index if not exists client_accounts_platform_fingerprint_idx
    on client_accounts (platform, secret_fingerprint);

-- ROLLBACK (only after scripts/encrypt_client_secrets.py --rollback, and only
-- if no row has had its plaintext dropped):
--   alter table client_accounts drop column if exists secret_fingerprint;
--   alter table client_accounts drop column if exists secret_last4;
--   alter table client_accounts drop column if exists access_token_enc;
