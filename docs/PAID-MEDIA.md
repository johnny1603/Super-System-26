# Paid media — Google Ads specs, craft and the ליאור → יואב handoff (2026-11-28)

The same architecture as social: a machine-readable source of truth
(`core/google_ads_formats.py`), a craft layer (`knowledge/paid-media-craft.md`)
and a contract between the two agents (`core/media_order.py`).

## 1. The table — `core/google_ads_formats.py`

Every Google Ads dimension, ratio, character limit, asset count and file-size
cap lives there and nowhere else. `tests/test_google_ads_formats.py`
(`OneSource`) tokenises every module of `core/` and `agents/` and fails on one
written anywhere else. `agents/google_ads_agent.py` reads its responsive-search
limits from it (they were literals there).

**Verified 2026-11-28. Re-verify by 2027-02-28** (`VERIFIED`, `REVERIFY_BY`).
Past that date `stale()` is true and every order and delivery check carries
`stale_line()` — the table says it may be out of date instead of being trusted.

| slot | ratio | recommended | minimum |
|---|---|---|---|
| landscape | 1.91:1 | 1200 × 628 | 600 × 314 |
| square | 1:1 | 1200 × 1200 | 300 × 300 |
| portrait | 4:5 | 960 × 1200 | 480 × 600 |
| vertical | 9:16 | 1080 × 1920 | 600 × 1067 |
| logo_square | 1:1 | 1200 × 1200 | 128 × 128 |
| logo_landscape | 4:1 | 1200 × 300 | 512 × 128 |

| campaign type | images | logos | text | file |
|---|---|---|---|---|
| `display` (responsive display ad) | landscape 1–15 (5 rec) REQUIRED, square 1–15 (5 rec) REQUIRED, portrait optional | square 1–5 REQUIRED, landscape 1–5 recommended | headline 30 × 5, long headline 90 × 1, description 90 × 5, business name 25 | JPG / PNG, 5 MB; **no GIF** |
| `pmax` (per asset group) | the same three ratios, 20 in total | 5 in total | headlines 3–15 @30 (one ≤ 15), long headlines 1–5 @90, descriptions 2–5 @90, business name 25 | JPG / PNG, 5 MB; video ≤ 15, ≥ 10 s |
| `demand_gen` | + vertical 9:16 (Shorts), 20 per ad | square, **150 KB** | headline 40 (one ≤ 30), description 90, business name 25; carousel 2–10 cards, one ratio, card headline 40 | JPG / PNG, 5 MB |
| `youtube` | — | — | — | a video, by URL |
| `search` | — | — | headlines 30 (3–15), descriptions 90 (2–4), path 15 × 2 | — |

Also in the table: the 20 uploaded static banner sizes (150 KB each) with the
six best-performing ones (`TOP_BANNERS`), the YouTube ad formats and their
lengths (`VIDEO_FORMATS`, `video_length_problems`), the video ratios and the
300 × 60 companion banner.

What the module does with it:

- `required_assets(campaign_type, concepts, banners, video)` — the media rows of
  an order: **every** ratio the type takes × `concepts`, its logo slots, a video
  where it needs one. Each row carries its own size, cap and file types.
  `max_concepts` keeps it inside the platform's ceilings (pmax: 6 per ratio).
- `minimum_to_serve(campaign_type)` — what the platform needs before anything
  serves. `minimum_source` says where it comes from: `stated` (pmax — the
  verified table gives it), `required_marks` (display — the slots marked
  REQUIRED), `derived` (demand_gen, youtube, search — the table states none, so:
  one of each thing the type cannot run without).
- `check_file(row, bytes)` — a produced file against its row, measured from the
  bytes: real type (magic bytes, never the name), real pixels, real weight.
- `check_text(campaign_type, texts)` — every limit, named per field and index.
  `char_count` counts the way Google does (a full-width character is two).
- `video_reference_problems(asset)` — **a video cannot be uploaded into a
  campaign as a file.** `VIDEO_WORKFLOW` is two required steps: upload to the
  client's YouTube channel, then reference by URL. A video asset without a
  YouTube URL is not deliverable, whatever file it has.

Decisions made here that are not Google's (flagged so they can be changed):

- The pasted spec gives `search` only ceilings ("up to 15 / up to 4"). The
  floors (3 headlines, 2 descriptions) are the ones `google_ads_agent`'s
  validator already enforced before the API call.
- Where the spec gives a responsive display count as "× 5", it is read as a
  ceiling of 5 with a floor of 1.
- `CROP_TARGETS` (which crops a slot must survive) — see §2.

## 2. The craft layer — `knowledge/paid-media-craft.md`

What the table cannot encode. Five rules (§2.1–2.5 there): crop survival, text
belongs in text fields, the 150 KB problem, coverage beats polish, brand
discipline. The file holds **no numbers of its own** — a size written there
would be a second source (`tests/test_paid_media_checks.py` fails on one).
It carries the table's two dates. Two of the rules are code, not advice:

### Crop survival — `core/crop_survival.py`

`check(content, slot, subject_box=None)` takes the PRODUCED file, makes the
centre crops `google_ads_formats.CROP_TARGETS` lists for its slot and measures
how much of the subject each crop keeps.

| slot | cropped to |
|---|---|
| landscape | 1:1 and 4:1 |
| square | 1.91:1 |
| portrait | 1:1 |
| vertical | 4:5 |

How the subject is found (pixels only — no model, no network, no cost): the
border ring is the background (its median colour and its own spread, so a
textured backdrop is not a subject); a pixel's distance from that is its
weight; an image with no colour separation falls back to its edges. A crop
survives when it keeps `MIN_RETAINED` (70%) of the weight. When the producer
knows where the product is it passes `subject_box`, and that box must sit
`MIN_BOX_INSIDE` (90%) inside every crop as well. No findable subject = a
failed check (it fails closed). `crops()` returns the crops themselves for a
human to look at; `line()` is the log line.

Limits, said plainly: this is not understanding. It cannot tell a product
from a brighter object beside it, and "readable" is approximated by "still in
frame". The two thresholds are judgment, not a Google spec, and have not been
tuned against real ad images.

The spec said "every display image must survive a centre crop to square and
to 4:1". That is applied literally to the **landscape** image, which is the
one Google cuts that way. Applied literally to a square or a portrait image it
would fail nearly everything (a 4:1 strip keeps a fifth of a portrait), so
those slots are held to the crop that actually happens to them (table above).
Change `CROP_TARGETS` to change it.

### The 150 KB problem — `core/ad_compress.py`

`to_cap(content, max_bytes, file_types, what, floor=None)`: a file already
within its cap is returned untouched; otherwise every encoding the slot takes
is tried (JPEG at the highest quality that fits — a search, never one guess —
a 256-colour PNG, a lossless PNG), each one's damage is measured against the
original (PSNR), and the least damaged that fits wins. None under the cap at
`MIN_PSNR` (33 dB) or better → `CannotReachCap`, naming the file, the cap and
how close the best attempt came. JPEG is never pushed under `MIN_QUALITY`
(60). A cut-out logo is never flattened to JPEG. A banner is never resized;
a slot with a minimum size (`floor`) may be stepped down, and the result says
so.

PSNR is a stand-in for "no visible degradation", not a pair of eyes; 33 dB is
a judgment call.

## 3. The ליאור → יואב handoff — `core/media_order.py`

Yoav (Google Ads) decides the campaign. Lior (media) decides how the creative
looks. The copy step writes the words. Three steps, three modules, and none of
them is handed another's half.

| step | who | module | gets | returns |
|---|---|---|---|---|
| 1. media order | Yoav | `media_order.issue` (decision: `media_order_actions.decide`) | the client's ask + what we know | the order |
| 2. text assets | copy | `ad_copy.write` | `copy_brief(order)` — no media row | texts within Google's limits |
| 3. files | Lior | `paid_media_production.produce` | `media_brief(order)` — no text slot | delivery manifest + files |
| 4. handover check | code | `media_order.hand_over` | order, manifest, the files' bytes | handed over, or `IncompleteDelivery` |
| 5. cross-check | Yoav | `media_order.yoav_verify` → `verify_minimum` | the files' bytes + the texts | verified / rejected |

### 3.1 The media order

`campaign_type` (pmax | demand_gen | display | youtube | search), `objective`,
`client_id`, `audience_segment`, `landing_page_url` (https only), `offer`,
`required_assets`, `deadline` — plus `palette` (the ONE brand variant the
segment resolves to, `post_brief.resolve_palette`; a segment that lands on no
single variant = `OrderError("audience_unresolved")`, a question to the client,
never a guess) and `specs` (the table's verification date and its stale
warning).

`required_assets` is `google_ads_formats.required_assets(...)` — nobody picks
a size. Each concept (a distinct picture) is ordered in EVERY ratio the type
takes; `DEFAULT_CONCEPTS = 3` is OUR choice (coverage, craft §2.4), capped by
the table's per-ratio and total maximums.

The decision call may return a landing page only if that address was in the
data it was shown or in the client's words; anything else is dropped and the
client is asked.

### 3.2 Text assets are copy

`core/copy_budget.ads_budget(type)` = Google's limits from the table, handed to
the copy step where the copy is written; `ads_fit` = the exact check of the
copy as written (same mechanism as a slide's budget; the constraint is the
platform's limit instead of the template's). One repair round that feeds the
named violations back, then `CopyFailed`. Copy is never cut to fit.
`ad_copy.write` refuses a brief that carries media fields;
`paid_media_production.produce` refuses one that carries text fields. Image
prompts go through `post_brief.assert_no_hebrew`.

### 3.3 The delivery manifest, checked from the files

`check_delivery(order, manifest, files)` counts a manifest row only when its
file exists and passes every check, then holds the count against the order:

- present (`missing`, `no_file`) — by slot, "2 of 3";
- within the size cap, the right ratio, minimum size, file type
  (`google_ads_formats.check_file` on the bytes);
- crop survival (`crop_survival.check` on the bytes, for the row's crop targets);
- a video is a YouTube URL (`video_file_not_url`, `video_bad_url`);
- the palette is the segment's variant (`palette_variant`), and any colour code
  drew is in it (`palette_colors`).

Nothing the manifest says about its own files is read. An incomplete set
raises `IncompleteDelivery`: the order is stored `incomplete` with the named
problems, `notify` is not called, nothing reaches Yoav. `problem_lines` says it
in the client's language.

How production fills it: one picture per concept in the widest ratio, on the
client's own image tool; other ratios are cut from it when the centre crop is
big enough and keeps the subject (measured), otherwise generated; each file is
fitted, compressed to its cap and crop-checked; a failed crop check is
regenerated within `RETRIES` (2) per order. Logos are the client's logo file,
fitted with clear space — never generated; no logo file = no logo row, and the
check names it.

### 3.4 Yoav's own check

`verify_minimum(campaign_type, delivered files, texts)` reads only the table's
`minimum_to_serve`, the files' bytes and the texts — not the order's rows, not
the manifest's check result. It runs only on a handed-over order.

### 3.5 In the chat — `prepare_campaign` (`core/media_order_actions.py`)

"תכינו קמפיין" → the manager (or Yoav / Lior) proposes `prepare_campaign`, every
parameter optional. `prepare`: package, brand brief + craft library, Yoav's
decision, the order is test-issued, the image tool is connected (else its one
card). The card shows the decision and what will be made. One click runs
order → copy → production → handover check → Yoav's check. Copy runs first: it
costs no credits, and a campaign whose copy cannot be written must not spend
them. The result line is built from the result: ready (counts), incomplete or
rejected (each missing thing by name). Stop mode: `STOP_FINISH`.

Storage: app_settings `media_orders:<client_id>` (newest 20, no bytes); files
in the client's Drive media folder under `ads/<order id>/`.

### Not built, not verified

- **Nothing is uploaded to Google Ads.** No asset upload, no asset group, no
  campaign. The chain ends at a verified set in Drive.
- **Internal accounts only** (`internal_only: True`) until one live run; opening
  it to paying clients is the owner's decision.
- **Never run live**: not against the image tool, a real brand brief, Drive or
  the model. Tests use fakes for all four.
- **Video**: not produced. Uploads in this system are always Private, and an ad
  needs a public or unlisted video, so a `youtube` order (and the optional
  video of the other types) cannot be completed by the system — it fails by
  name. There is no "attach a URL to the order" path yet.
- **Uploaded banners** (the 150 KB sizes): the table, the compressor and the
  checks cover them; nothing produces them, and the card never orders them.
- **Palette check**: the variant is recorded by the producer and compared; code
  draws no colours into these images, so there is no pixel-level palette
  measurement of a generated photo.
- The agents do not yet read orders back (`media_order.summary` exists, nothing
  feeds it to `client_state`).
