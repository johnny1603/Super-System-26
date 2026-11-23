# Brand brief — in the database, drafted at onboarding (2026-11-24)

`core/brand_brief` (storage, versions, review) + `core/brand_intake` (the intake agent). Schema:
`knowledge/brand-brief.schema.md` (stays in the repo).

## Storage

app_settings `brand_brief:<client_id>` = `{"versions": [{v, at, by, kind, status, text, palette, gaps, sources}],
"draft_job": {...}}`. The newest version is the brief; every save is a new version (30 kept). `kind`: migrated /
drafted / edited / approved; `status`: draft / approved. Cleared with the client.

## Migration (client 27)

A client with no row but a repo file (`knowledge/brands/<id>.md` + `<id>.palette.json`) is seeded on first read,
byte-for-byte (kind `migrated`, status from the file's own Status line). After that the file is not read for
that client. Database unreadable → the file is used read-only, so 27 behaves the same either way. The two files
stay in the repo as the seed; they can be deleted once the Control Room shows 27's brief as "מסד הנתונים".

## Onboarding draft

Checkout and a client created in the admin queue ONE draft (`request_draft`); an agent that needs a brief that
does not exist queues one too. The every-minute tick (`/api/tiktok/uploads/run`) runs queued drafts inside its
request (budget left > 75 s; never a background task), up to 3 attempts. "לכתוב טיוטה עכשיו" in the Control
Room runs it inside that request. Sources: the intake answers on the client's linked lead, the website (a link
in the answers, or the connected WordPress / WooCommerce site; an email's domain is not used), the public social
pages (title / description meta only; a login wall is a gap), descriptions of connected Drive assets. ONE model
call; every field line carries [stated] / [observed] / [inferred] (an unmarked line gets [inferred]). A draft
never overwrites an existing brief.

## Agents

`post_brief.load_context`: the craft library is still required (missing = ContextMissing). The brief: a draft is
used as it is (its Status line tells the agents; [inferred] is direction only); NO brief = `MISSING_NOTE` (work
from the request, neutral choices, no factual claims, say what you lacked) — never a refusal. `brand_lacks`
lists what is missing (the brief, the palette).

## Palette

Only colours seen on the site (theme-color, inline / same-host CSS) may fill a role (bg, fg, accent, muted);
anything else is dropped and recorded as a gap. `slide_spec.palette_roles`: no palette, or a role not determined
→ NEUTRAL for that role; a palette that exists without the requested variant → PaletteMissing (a real mismatch).

## Control Room

Client → "🧾 תדריך מותג — סקירה, עריכה ואישור" (`/api/admin/brand-brief/{id}`, edit, approve, draft; admin
session only): status (draft awaiting approval / approved / none), version and source, counts per confidence
mark, the palette with swatches (missing roles shown with their neutral colour) and its edit, the gaps, every
section's fields with their marks, a section editor (the whole text is saved as a new version), approve (rewrites
the Status line; edits are kept), version history. Tests: `tests/test_brand_brief.py`.