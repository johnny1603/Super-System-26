"""One-time cleanup PROPOSAL: find (and optionally redact) likely secrets in
chat transcripts stored BEFORE redaction existed (2026-10-18).

Prints COUNTS ONLY — never a value, never a message.

USAGE (Cloud Shell, from the repo root, SUPABASE_URL + SUPABASE_SERVICE_KEY set):

    python scripts/redact_stored_chats.py            # dry run (default): counts per table
    python scripts/redact_stored_chats.py --apply --confirm
                                                     # rewrite matching rows with [REDACTED]

Tables scanned: client_communications.content, lead_messages.content,
admin_agent_chat.content, leads.answers (every string inside the JSON).
--apply is irreversible for the redacted substrings (that is the point) —
take a Supabase backup first. Idempotent: a redacted row no longer matches.
"""
import argparse
import os
import sys

sys.path.insert(0, ".")

from core.redaction import redact, redact_obj  # noqa: E402

PAGE = 500
TARGETS = (
    ("client_communications", "content"),
    ("lead_messages", "content"),
    ("admin_agent_chat", "content"),
    ("leads", "answers"),
)


def _db():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _rows(db, table, column):
    start = 0
    while True:
        batch = (db.table(table).select(f"id,{column}").order("id")
                 .range(start, start + PAGE - 1).execute().data or [])
        yield from batch
        if len(batch) < PAGE:
            return
        start += PAGE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    if args.apply and not args.confirm:
        sys.exit("refused: --apply rewrites stored transcripts — add --confirm (after a backup)")

    db = _db()
    for table, column in TARGETS:
        scanned = flagged = changed = 0
        try:
            for row in _rows(db, table, column):
                scanned += 1
                value = row.get(column)
                cleaned = redact_obj(value) if isinstance(value, (dict, list)) else redact(value)
                if cleaned != value:
                    flagged += 1
                    if args.apply:
                        db.table(table).update({column: cleaned}).eq("id", row["id"]).execute()
                        changed += 1
        except Exception as e:
            print(f"{table}: unreadable ({type(e).__name__}) — skipped")
            continue
        print(f"{table}.{column}: scanned {scanned}, likely-secret rows {flagged}"
              + (f", redacted {changed}" if args.apply else ""))


if __name__ == "__main__":
    main()
