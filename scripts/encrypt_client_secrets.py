"""Data migration: encrypt every credential in client_accounts.

Prints COUNTS ONLY — never a secret, never part of one, never a row's value.

USAGE (Cloud Shell, from the repo root, with SUPABASE_URL,
SUPABASE_SERVICE_KEY and SECRETS_ENCRYPTION_KEY set — the same key the
service uses):

    python scripts/encrypt_client_secrets.py                 # dry run (default): counts only
    python scripts/encrypt_client_secrets.py --apply         # encrypt rows that are still plaintext-only
    python scripts/encrypt_client_secrets.py --verify        # every encrypted row decrypts (and matches plaintext if still present)
    python scripts/encrypt_client_secrets.py --drop-plaintext --confirm
                                                             # blank plaintext ONLY where --verify passes for that row
ROLLBACK:
    python scripts/encrypt_client_secrets.py --rollback      # before drop: clear the encrypted columns (code falls back to plaintext)
    python scripts/encrypt_client_secrets.py --restore-plaintext --confirm
                                                             # after drop: decrypt back into the plaintext column (emergency only)

Every mode is idempotent: --apply skips rows that already have ciphertext,
--drop-plaintext skips rows already blank, --rollback skips rows without
plaintext (they would lose their only copy; counted and refused).
"""
import argparse
import os
import sys

sys.path.insert(0, ".")

from core import secrets_vault as vault  # noqa: E402

PAGE = 500
COLUMNS = "id,client_id,platform,access_token,access_token_enc"


def _db():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _rows(db):
    start = 0
    while True:
        batch = (db.table("client_accounts").select(COLUMNS).order("id")
                 .range(start, start + PAGE - 1).execute().data or [])
        yield from batch
        if len(batch) < PAGE:
            return
        start += PAGE


def _decrypts(row) -> tuple:
    """(ok, matches_plaintext_or_None) — never returns the value."""
    try:
        value = vault._decrypt(row["access_token_enc"], row["client_id"], row["platform"])
    except Exception:
        return False, None
    plain = row.get("access_token") or ""
    return True, (value == plain) if plain else None


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--drop-plaintext", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    mode.add_argument("--restore-plaintext", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if (args.drop_plaintext or args.restore_plaintext) and not args.confirm:
        sys.exit("refused: this mode changes the plaintext column — add --confirm")
    if args.apply or args.verify or args.drop_plaintext or args.restore_plaintext:
        try:
            vault._key()
        except vault.VaultUnavailable as e:
            sys.exit(f"refused: {e}")

    db = _db()
    counts = {"rows": 0, "empty": 0, "plaintext_only": 0, "encrypted_only": 0,
              "encrypted_and_plaintext": 0, "changed": 0, "skipped": 0,
              "decrypt_ok": 0, "decrypt_failed": 0, "plaintext_mismatch": 0}

    for row in _rows(db):
        counts["rows"] += 1
        plain, sealed = row.get("access_token") or "", row.get("access_token_enc") or ""
        if not plain and not sealed:
            counts["empty"] += 1
        elif plain and not sealed:
            counts["plaintext_only"] += 1
        elif sealed and not plain:
            counts["encrypted_only"] += 1
        else:
            counts["encrypted_and_plaintext"] += 1

        if args.apply:
            if plain and not sealed:
                db.table("client_accounts").update({
                    "access_token_enc": vault.encrypt(plain, row["client_id"], row["platform"]),
                    "secret_last4": vault.last4(plain),
                    "secret_fingerprint": vault.fingerprint(plain),
                }).eq("id", row["id"]).execute()   # plaintext KEPT: the rollback path
                counts["changed"] += 1
            else:
                counts["skipped"] += 1

        elif args.verify and sealed:
            ok, match = _decrypts(row)
            counts["decrypt_ok" if ok else "decrypt_failed"] += 1
            if match is False:
                counts["plaintext_mismatch"] += 1

        elif args.drop_plaintext:
            if plain and sealed:
                ok, match = _decrypts(row)
                if ok and match:
                    db.table("client_accounts").update({"access_token": ""}).eq("id", row["id"]).execute()
                    counts["changed"] += 1
                else:
                    counts["skipped"] += 1   # never drop a copy that doesn't verify
            else:
                counts["skipped"] += 1

        elif args.rollback:
            if sealed and plain:
                db.table("client_accounts").update({
                    "access_token_enc": None, "secret_last4": None, "secret_fingerprint": None,
                }).eq("id", row["id"]).execute()
                counts["changed"] += 1
            else:
                counts["skipped"] += 1   # no plaintext copy -> clearing would lose the secret

        elif args.restore_plaintext:
            if sealed and not plain:
                try:
                    value = vault._decrypt(sealed, row["client_id"], row["platform"])
                except Exception:
                    counts["decrypt_failed"] += 1
                    continue
                db.table("client_accounts").update({"access_token": value}).eq("id", row["id"]).execute()
                counts["changed"] += 1
            else:
                counts["skipped"] += 1

    mode_name = next((m for m in ("apply", "verify", "drop_plaintext", "rollback", "restore_plaintext")
                      if getattr(args, m)), "dry_run")
    print(f"mode: {mode_name}")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
