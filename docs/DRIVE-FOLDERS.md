# Connected Drive folders — the client's own real assets (2026-11-23)

The business owner connects folders that already exist in their Google Drive. The system
classifies them, indexes the photos and videos cheaply, and the media agent picks real files
from them on its own. The owner is never asked to pick a file.

Code: `core/drive_folders.py` (connections, roles, errors), `core/drive_assets.py` (index,
cap, `find_assets`), `agents/media_agent.py` (`review_brief`, `generate_from_brief`,
`choose_real_asset`, `_use_real_asset`). Tests: `tests/test_drive_folders.py`.

## Access model — no OAuth

The client shares the folder with our existing service account
`drive-archiver@super-system-500410.iam.gserviceaccount.com` (as Editor; in a Shared Drive, as a member with Content manager — Contributor cannot trash) and pastes the
folder link. Every Drive call runs `drive_storage.as_service_account()` with
`supportsAllDrives` / `includeItemsFromAllDrives`. **No Drive scope was added to the OAuth
app** (it is under Google verification review).

Supported: My Drive folders, folders shared with them, and folders inside a Shared Drive. In a
Shared Drive, sharing the folder alone is sometimes not enough: the service account must be a
member of the Shared Drive. The errors are distinct Hebrew lines (`drive_folders._texts`):
`not_shared` (with the address and a copy button), `shared_drive_member`, `bad_link`,
`not_found`, `not_folder`, `trashed`, `own_storage`, `connected_elsewhere`, `drive_busy`,
`our_side`. Never a generic "no access".

## Roles

product_catalog, customer_photos, brand_assets, raw_materials, social_output, archive.

The role ranks and filters assets in `find_assets` and decides which folders the agents draw
from. It does NOT restrict access: every connected folder is read_write (2026-11-23, the
manager's decision), and nothing in the code refuses a write into one.

## Referenced, not copied (2026-11-23, `core/asset_refs`)

A real asset the agents choose stays where it is: the Social-queue item carries the file's OWN
Drive id. Selection, ranking, the preview/thumbnail, the approval card, download, the player and
the TikTok / YouTube sends (they stream byte ranges straight from Drive) all work from that id
plus Drive's own thumbnail link. Ownership checks (`drive_mirror._assert_in_client_tree`,
`drive_mirror.thumbnail`, `drive_download._verify_in_client_tree`) accept an id that is in the
client's connected-folder index (`asset_refs.is_connected`: not removed, folder not excluded).

Copies happen in exactly two places:

1. **Working copy, only when an operation needs one.** Today: Meta publishing needs a PUBLIC link,
   and making the client's original public would change their sharing — so
   `media_agent.prepare_for_publishing` copies it into our media folder `working/`, makes the copy
   public, and `meta_content_agent.publish` deletes it when the call returns (success or failure).
   An unreleased copy is deleted by the daily `drive-assets-scan` run after 24 h
   (`sweep_working_copies`). `asset_refs.working_copy()` is the context manager for any future step
   that must hold bytes (compositing a photo into a slide — the slide renderer does not do that today).
2. **Publish snapshot**, client setting `drive_snapshot_on_publish` (default ON; Control Room →
   client settings to switch off): when a post is published (`social_queue.mark_published`, or every
   channel delivered `done`), a referenced asset is copied into the client's connected
   **social_output** folder (`appProperties.uallak_snapshot_of`, idempotent) and recorded on the item
   as `snapshot`. No social_output folder connected → `snapshot.skipped = no_social_output_folder`.

Nothing else is ever copied.

A **social_output** folder (a root, or a subfolder given that role) is never indexed
(`drive_assets.NOT_INDEXED_ROLE`): the walk skips it and drops anything indexed there before, so
the snapshots are not counted again as client assets. The root shows status `ok`.

## Change detection, not daily walks (2026-11-23)

The scheduled job `drive-assets-scan` still runs every day: retention and the stuck-working-copy cleanup
first, then `drive_assets.daily_scan` → `check_changes` per client. It no longer walks folders:

- Each root connection stores a Drive **changes page token** (`changes_token`, `changes_drive_id` — its
  Shared Drive, or the service account's own corpus), taken when the folder is walked, before the listing.
- The check asks `changes.list` what changed since the token. **Nothing changed → nothing else runs** (no
  listing, no enumeration). Changed files → only those are upserted and new / changed ones described.
  Removed / trashed / moved out → out of the index.
- A missing token, a token Drive refuses (400/404/410), a new or moved-in subfolder or a trashed subfolder →
  that root is walked ONCE (`job.walk_only`), which stores a fresh token. Other errors: try next time.
- **Chat trigger** (`core/drive_index_chat`, hooked in both chat paths of `support_agent`): "העליתי את
  התמונות" / "I uploaded the photos" (an upload verb + a files/folder word, never "not yet") → the folder is
  walked now (`index_now`, mode `chat`, first step in the background). Several folders and none named → ONE
  short question ("לאיזו תיקייה העליתם — X או Y?"), answered within 30 min by naming one or "all".
- **First use** (`ensure_indexed`): an agent selecting from a connection that was never indexed indexes it
  right then (`find_assets`, `has_pool`, `media_agent._find_real`).
- **Use time** (`verify_usable`): before a real asset goes into a post, the file must exist, not be trashed and
  still sit in a connected folder; otherwise it is marked `state: missing` (out of selection, the library and
  the counts until a walk / change sees it again) and the agent chooses again without it
  (`media_agent.REAL_ASSET_TRIES`). A post never fails because one indexed file disappeared.

## Descriptions (2026-11-23, `core/asset_describe`)

Every vision-described asset stores `description` (1–2 sentences), `tags` and `attributes`: subject; colours
(dominant / accent); framing + angle; background; person (present, what is visible); lighting; mood; visible
text; branding; sharp / well exposed; where text could be overlaid (`text_space`). The prompt is
category-aware (the brand brief's `category:` → fashion_footwear / food / beauty / home / jewelry /
services_people, else default). Tags come from a controlled vocabulary (shared base + category + what this
client's index grew, `state.vocab`); code maps near-duplicates onto the existing tag. Each asset stores
`prompt_version` (`asset_describe.PROMPT_VERSION`); `outdated()` lists older ones and
`POST /api/admin/drive-assets/{id}/redescribe` re-describes only those (group copies follow; each counts
against the allowance). The chooser sees the attributes; search matches them.

## Thumbnails

Drive's `thumbnailLink` for a file that isn't public only answers a credentialed request, so the media
library never points the browser at it: `<img>` → `/api/client/media-inventory/thumb/{id}` → fetched
server-side with the service account (`media_inventory.thumbnail`), cached in memory per content version
(24 h, 600 entries) and in the browser (`max-age=86400`). No file is ever made public for it.

**In the chat:** `client_state.connected_folders` — per connected folder: name, role, status,
item counts (all / described / images / videos), subfolders with their own role, excluded
subfolders; plus totals, whether an index run is going and the free items left. A summary only —
the files themselves are searched by `find_assets` when something is produced. Older posts produced before this change still point at their earlier
copy in our media folder (source `real_copy` in the media library).

## Adding a folder (settings → "תיקיות דרייב מחוברות")

1. Paste the link → `POST /api/client/drive-folders/preview`: the folder is read (listing
   verified), the role inferred from its NAME (Hebrew + English keywords,
   `drive_folders.NAME_RULES`, customer photos checked first), else ONE vision call on up to 8
   thumbnails (`CLASSIFY_MODEL`). Nothing is saved.
2. The inferred role is pre-selected with a one-line Hebrew explanation; the client confirms or
   changes it → `POST /api/client/drive-folders/connect` reads the folder again (an unreadable
   folder is never saved), stores it and queues the first index run.
3. Subfolders (walked to depth 6, system/trash folders skipped) inherit their parent's role. Each
   can be overridden (`…/{id}/role`, empty = inherit again) or excluded (`…/{id}/exclude`). A
   subfolder whose name suggests another role is flagged, never changed silently.

## Indexing (cheapest first)

1. Tier 0, free: name, path, role, dates, size, dimensions, EXIF summary (camera, taken time,
   whether a location exists — never the coordinates). A descriptive file NAME
   (`descriptive_name`) is the description — no vision call.
2. Dedupe: dHash (64-bit) of the 512 px preview; within one folder, ≤ 10 bits apart = the same
   item from another angle. One is described; the others copy it with an angle note (cost 0).
3. Previews are Drive thumbnails at `=s512`, then downscaled with Pillow (≤ 512 px).
4. `CLASSIFY_MODEL`, one sentence + ≤ 5 tags, up to 8 images per call.
5. Newest first; `drive_assets_batch_items` per run (default 60). Older files are described on
   demand: the "אינדוקס עכשיו" button, a credit load, or an agent search that hits them
   (`find_assets(on_demand=True)`).

**Video:** the FIRST usable frame — ffmpeg (added to the Dockerfile) reads one frame at
0 / 0.5 / 1 / 2 / 4 / 8 s with Range requests and the first non-black one wins; fallback
Drive's own thumbnail if not black. One frame = the cost of one image. Duration, size and
format come from Drive's metadata; `media_type: "video"`. (This is NOT the YouTube metadata
path, which still never looks at video content.)

## Free cap → quote → billing

`client_settings.drive_assets_free_items` (default 150, editable per client in the Control
Room). The ACTUAL cost is metered from the model calls' usage (`claude_json.collect_usage`) and
the running average per item is stored (`meter.avg_usd`; `meter.first_run` keeps the first
run's numbers). At the cap the run stops — nothing more is spent — the quote for the
remaining items = remaining × measured average, and ONE `billing` team request opens (client,
folders, remaining, cost per item, total). The client sees a plain Hebrew line. The team bills
and loads credits: `POST /api/admin/drive-assets/{client_id}/credits {"items": N, "usd": x,
"note": ""}` (admin session) — indexing resumes from where it stopped. Team view:
`GET /api/admin/drive-assets/{client_id}`.

## Runs

Cloud Run throttles CPU after a response, so the work moves in steps: the settings section's
poll (`POST /api/client/drive-folders/step`, ≤ 20 s), the every-minute tick
(`/api/tiktok/uploads/run` leftover budget) and the daily job **`drive-assets-scan`** 04:15
(`GET /api/drive/assets/scan`, `scripts/schedule_jobs.sh`). The daily run indexes only NEW or
CHANGED files (md5 changed = new content; a rename or move only updates metadata) and never
re-describes a described file. Lease `drive_assets_lock:<id>`.

## The agents

`drive_assets.find_assets(client_id, roles, query, media_type, limit)` → ranked
`file_id, description, tags, role, media_type, folder, …`
(tags ×3, description ×2, name/path ×1; excluded subfolders never appear).

- `media_agent.generate_from_brief` searches FIRST for every non-text asset; the agent
  (`choose_real_asset`, a cheap text call) chooses one or none; a chosen file is REFERENCED by
  its own id (never copied — §Referenced, not copied), credits 0, no image tool needed.
- `review_brief`: a proof slide, or a product_carousel / before_after / customer_story asset,
  with no real match = an objection to the social agent (never a generated stand-in).
- Customer photos are used like any other real asset; nothing blocks their publication.
- Cards show "Source: folder X (role)"; generated frames keep the `concept` flag.
- `produce_post` asks for the image tool only when an asset must be GENERATED.

## Storage

app_settings `drive_folders:<id>`, `drive_assets:<id>`, `drive_assets_lock:<id>` — cleared with
the client (admin_service, client_deletion, deletion_guard). No migration.

## Credits screen (2026-11-24)

Control Room → client → "💳 קרדיטים לאינדוקס תיקיות דרייב" (`GET/POST /api/admin/drive-assets/{id}/credits`,
admin session only; `drive_assets.credits_view`). Shows the balance (free + loaded − used), the open quote
when the cap stopped indexing (folders, items left, measured cost per item, total), the measured cost per
item of the LAST run (`meter.last_run`, written when a run finishes) and the all-runs average, the job
(resumed / queued, items left) and every credit load (amount, when, who, note). Loading: amount + optional
note + "loaded by" (the admin session has no identity, so the name is typed and remembered in the browser;
stored as `admin:<name>`), ONE confirmation, then `load_credits` — indexing resumes where it stopped.
Tests: `tests/test_credits_admin.py`.
