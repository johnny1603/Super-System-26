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
