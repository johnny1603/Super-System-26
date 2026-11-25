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

## Fields and confidence (fix, 2026-11-24)

The stored `text` was always the file byte-for-byte; the Control Room's first parser read only the FIRST line
of each `- key: value`, dropped nested lists, kept `**`, and looked for a mark only on that first line (most of
27's marks sit on a continuation line). `parse_fields` (PARSER_VERSION 2) now: joins continuation lines, keeps
lists (nested bullets, numbered lines, key-less bullets under an empty `key:`) as lists, keeps key-less bullets as
their own field (no line dropped), strips markdown, finds every mark anywhere in a field or item (also across a
line break). Each version stores `fields` (the parsed structure) next to `text`.

Unmarked = inferred: a field / item with no mark is `effective: inferred`. The agents get the brief as written
plus `CONFIDENCE_RULE` and the list of every unmarked field / item (`confidence_footer`) — direction only, never a
factual claim. A migrated version without the new fields is re-migrated ONCE from the repo file automatically,
only while it is still the newest version (never over edits); "העברה מחדש מהקובץ" does it by hand. The screen
shows whether the stored text equals the file.

## The client's branding page "מיתוג" (2026-11-26, `core/brand_page`)

Client dashboard → מיתוג. It is a VIEW of the brief, not a store (`knowledge/client-governance.md` §7):

| On the page | Where it is written | Who reads it |
|---|---|---|
| Brand colours, per audience variant (client 27: `black + red` private, `black + gold` business — shown as two blocks, never one palette) | the brief's `palette.variants[label][role]`, `status: stated` | `brand_brief.roles_for` → the slide renderer |
| Typefaces (headline / body) | `visual_identity.typefaces` in the text, `[stated — the client, branding page, date]` | both content agents (the brief in full) |
| Tone: says / does not say | `voice.tone_attributes` / `voice.avoid` | both content agents |
| Logo versions (full, mark only, on light, on dark) and existing assets | files in the client's media folder, sub-folder `brand` (appProperties `uallak_kind: brand_file`, `brand_version`, measured width/height/alpha); listed in the text under `assets.logo_files` with their file ids | agents (a slide spec's `logo.asset_ref`) |

Every save is a new brief version by `client` (history in the Control Room's brief view). The status does not change.

- **Accepted as it is.** An upload is refused only for what a file system needs (empty, not PNG/JPG/WEBP/SVG/PDF, over 15 MB, 40 files). Never for quality, never replaced, never a draft.
- **One friendly note** (`assessment`): only measured things — text or accent contrast of colours the client STATED, a logo under 500 px wide, no transparent logo. Built from fixed fragments (what works → the specific point → the investment and cost line), never model text. Dismissed once (`page.assessment_dismissed_at` on the brief's own row) it never returns, whatever changes.
- **No branding at all** (`lead`): no note; the page opens with the branding studio (core/branding).
- Routes `/api/client/brand-identity…`; staff see it read-only. `tests/test_brand_page.py`.

Not done here: the renderer still draws only its bundled fonts (a stated typeface reaches the agents as text); the studio's approved kit (`brand_kit:<id>`) is still its own object, shown on the page as "saved" but not merged into the brief's palette; a client cannot add a NEW audience variant from the page (the brief's text names them).
