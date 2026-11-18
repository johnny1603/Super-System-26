# Languages: full separation (2026-11-19)

The client gets their own language (he, en, fr, ar, ru) from every surface. Hebrew is the source language and
the fallback, never the default for a client who chose another language.

## One source: `core/client_language`

| What | Where |
|---|---|
| The client's setting | `of(client_id)`. It reads `clients.language`, with a 60 s cache; an unreadable value gives `he` and is not cached. |
| A server-built line | `resolve(client_id, lang)`. An explicit supported `lang` wins, otherwise the setting. 33 functions moved from `lang="he"` to `lang=None` + `resolve`. |
| The reply language of a chat turn | `for_message(client_id, message)`. Hebrew, Arabic or Cyrillic script decides directly. Latin text uses the setting when it is en/fr, otherwise en. |
| Every chat model call | The payload gets `reply_language` (`payload(lang)`: code, name, direction). The system prompt gets `REPLY_LANGUAGE_RULE`: everything written for the client is in that language, with no drift back to Hebrew because the history is in Hebrew. |
| Direction and formats | `direction()` is RTL for he/ar only. `fmt_number`, `fmt_money` and `fmt_date` follow the language. |
| Hebrew-only items | `HEBREW_ONLY_NOTE` / `hebrew_only_note(lang)`: one line in the client's language. On the dashboard this is the handover document (`own_hebrew_only`). The terms are translated, and section 6 adds the "Hebrew prevails" line. The product has no tax forms today. |

Calls that already carry it:
- the support chat (both stages);
- the persona turns;
- the morning briefing, which also has 5-language code text, weekday names and the urgent sign-off.

## Server lines through tables

The rule: a line the server builds for a client lives in a 5-language table (`_five(...)` / `_txt(...)` / a dict
with he/en/fr/ar/ru) and is picked in the client's language. Swept in this round:

- **chat uploads:**
  - refusals (`PLAIN` + `plain()`);
  - proposal lines;
  - the saved line;
  - folder names;
  - the API also returns `message` in the client's language, and the dashboard prefers it over `message_he`.
- **store:**
  - errors (`ERRORS` + `error_text()`);
  - order statuses;
  - the one-order customer-details answer.
- **in-chat upgrade proposal** (`_format_proposal_reply`, with money formatted per language).
- **purchase-tracking note** on the conversion card.
- **weekly ideas line, payment-failure notice, filming-kit line, weekly media plan line, first-login fallback.**
- **TikTok inbox "what you do next":** this is model data, so it is now English. The model writes it in
  `reply_language`.

## Guard and ratchet: `core/i18n_gaps` + `tests/test_language_separation.py`

- `hardcoded(path)` is an AST scan for Hebrew string literals outside a language table. It skips:
  - regexes;
  - prompt and pattern constants;
  - docstrings;
  - English model descriptions that only quote a Hebrew name.
- **`CLIENT_FACING`:** these modules must be at zero, apart from the small `ALLOW` list.
  - `ALLOW` holds staff-only lines and functions (`file:@function`).
  - The test fails on a new one.
- **`KNOWN_DEBT`:** every other module that still has Hebrew literals, with its count today. The count may only go
  down.
  - It covers staff and operator pages and model data.
  - It also covers client lines **not swept yet**: `ownership`, `connection_catalog`, `landing_pages`,
    `avatar_video`, `drive_pull`, `vendor_tools`, `outreach_tracker`, parts of `api_server`, `website_agent`,
    `landing_page_agent` and others.
  - To finish a file: move its lines into tables, delete its `KNOWN_DEBT` entry, and add it to `CLIENT_FACING`.
- `table_gaps(path)` lists table slots that are missing, empty, or still Hebrew.

## The owner's gaps report

The report is at `/admin/i18n/` (`GET /api/admin/i18n/gaps`, admin session). It shows:
- the missing translations per language (server tables + client pages, from `scripts/i18n_report.py`);
- Hebrew outside the tables on client paths;
- the not-yet-swept files with their counts.

It is read-only, and makes no model or paid call.
