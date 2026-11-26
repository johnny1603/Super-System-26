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
| Typefaces (headline / body) — CHOSEN BY THE SYSTEM, approved by the client (no picker; docs/FONTS.md §4) | `visual_identity.typefaces` in the text, written when the client approves: `[stated — the client, branding page, date]` | the slide renderer (`brand_fonts.resolve`), both content agents |
| What may never be said about the business — ONE free-text answer (`core/brand_voice`) | `voice.never_say`, `[stated — the client, branding page / onboarding, date]` | EVERY model call made for the client (`claude_json` appends `brand_voice.rule`), and the brief in full |
| The tone — shown read-only, never edited here | `voice.tone_attributes` (the system's; drafted from the brief) | both content agents |
| Logo versions (full, mark only, on light, on dark) and existing assets | files in the client's media folder, sub-folder `brand` (appProperties `uallak_kind: brand_file`, `brand_version`, measured width/height/alpha); listed in the text under `assets.logo_files` with their file ids | agents (a slide spec's `logo.asset_ref`) |

Every save is a new brief version by `client` (history in the Control Room's brief view). The status does not change.

- **Accepted as it is.** An upload is refused only for what a file system needs (empty, not PNG/JPG/WEBP/SVG/PDF, over 15 MB, 40 files). Never for quality, never replaced, never a draft.
- **One friendly note** (`assessment`): only measured things — text or accent contrast of colours the client STATED, a logo under 500 px wide, no transparent logo. Built from fixed fragments (what works → the specific point → the investment and cost line), never model text. Dismissed once (`page.assessment_dismissed_at` on the brief's own row) it never returns, whatever changes.
- **No branding at all** (`lead`): no note; the page opens with the branding studio (core/branding).
- **Real work, never a sample (2026-11-27).** The page's sample slide (our own invented copy) is gone. `recent_work` shows the last 4 posts actually produced for the client (files in the Social tab that we made and the owner did not turn down, grouped per post, newest first), each with the colours and fonts its render manifest says were drawn — a post outside the brand palette says so. No posts yet = the card is not drawn at all.
- **The voice (2026-11-27, `core/brand_voice`).** The client does not curate the agents' tone: the tone editor is gone. The positive tone stays in the brief, owned by the system and applied without asking; the page shows it read-only (a closed "the tone we write in for you" block: the tags in the client's language + the reasoning). The client answers ONE question — "יש משהו שאסור לנו להגיד עליכם?" — as free text, for what only they would know (a competitor's phrase, a word that lands badly in their field, something that went wrong once), not for style. It is asked ONCE in onboarding (`interview_agent`: `captured.never_say`, their own words, `never_say_set` stops a second ask); the page shows only a short line of what is set with an edit link (`POST …/never-say`). The answer + the brief's older does-not-say list (`voice.avoid` / `avoid_words`, kept and enforced) are `brand_voice.rule(client_id)`, appended by `claude_json.safe_claude_json_call` to the system prompt of EVERY call made for that client (last, uncached). It is an instruction to every writer, not a text filter. English tags are translated for display once per change (`ensure_shown`, called by the page's GET route, cached on `page.voice_shown`; reading the page never calls a model; the rule keeps the stored words). `tests/test_brand_voice.py`.
- **A gap states what it costs (2026-11-27, `gaps` / `GAPS`).** Every empty part — each logo version, colours (none / our estimate), fonts, existing assets — carries one line saying what happens in practice without it ("בלי גרסה לרקע בהיר, אין לנו לוגו שמתאים לסטורי בהיר או לתמונת מוצר…"), in 5 languages. A part that is there has no line; files that could not be listed say nothing. A new slot on the page needs its line in `GAPS`.
- **The branding package (2026-11-27, `core/brand_package`).** The owner defined the package: **a logo, a branded website, and two landing pages with different purposes** — the page's three sections are those DELIVERABLES (`logo`, `website`, `landing_pages`), each with ONE line on what it does for the business. Typography and the visual language are not tiles: each section's "includes" says they are established along the way. A section opens to what it includes, "ask Johnny" (a tracked team request, topic `package_exception`; no price from code) and ONE real MOCKUP built from THIS client's material: their logo file (the version that suits the background), their palette variants, their actual product photos from the media library (`drive_assets.find_assets`, roles `product_catalog` / `customer_photos` / `raw_materials` — never our posts), their own latest copy, the brand's fonts. `website` = the top of a home page (navigation, hero, a row of their other products); `landing_pages` = two pages, private customers on the first palette variant and business on the second, each with its own photo, copy and call to action; `logo` = their mark as a system (on the brand colour, on light, as a profile picture, on a product photo, on a card) — with no logo file it is the business name in the brand's font and the caption says it is only a first sketch. NEVER a generic template: no product photo → `needs_photos` (told what is missing, nothing recorded, the owner still alerted). Rules unchanged: asked for explicitly, the credit cost shown first, `confirm=1` required by the server, then locked again (`sample_used`). Composed IN CODE (Pillow + the slide renderer's type engine, never a model) — hence 0 credits today (`SAMPLE_CREDITS`). The PNG is kept in the client's brand folder (`uallak_kind: package_mockup`, not a brand file) and its inputs are frozen in a snapshot on `page.package`. Every request, refused repeat, failed attempt and question raises an admin alert with "client {id}" and "buying signal". `tests/test_brand_package.py`.
- **The hero.** The page opens with the brand as it stands (logo on its own colour, each palette variant, the fonts as pictures, tone words) and how many parts are still open.
- Routes `/api/client/brand-identity…`; staff see it read-only. `tests/test_brand_page.py`.

Not done here: the renderer still draws only its bundled fonts (a stated typeface reaches the agents as text); the studio's approved kit (`brand_kit:<id>`) is still its own object, shown on the page as "saved" but not merged into the brief's palette; a client cannot add a NEW audience variant from the page (the brief's text names them).
