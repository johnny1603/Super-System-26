# Social formats — one table (2026-11-25)

`core/social_formats.py` decides every pixel size, aspect ratio and safe zone
of anything heading to a social channel. Nothing else does:
`tests/test_social_formats.py` (`OneSource`) tokenises every module in `core/`
and `agents/` and fails on a size, ratio or resolution literal outside it. The
one listed exception is the vendors' documented enums in
`core/media_gen_service` (what a model accepts is the vendor's fact, not our
choice).

## The table

| name | px | ratio | note |
|---|---|---|---|
| feed_portrait | 1080 × 1350 | 4:5 | the default for all feed output |
| feed_square | 1080 × 1080 | 1:1 | |
| feed_landscape | 1080 × 566 | 1.91:1 | |
| feed_tall | 1080 × 1440 | 3:4 | app-only, rejected by Meta's publishing API: `publishable=False`. It is the ratio asked of the image model before the crop. |
| vertical | 1080 × 1920 | 9:16 | story / reels / tiktok / shorts |
| reel_cover | 420 × 654 | 1:1.55 | |
| site_banner | 1920 × 1080 | 16:9 | not social; listed so no second table exists |

Safe zones on the vertical canvas (px): `meta_unified` 269 / 672 / 65 / 65
(top / bottom / left / right), `tiktok` 130 / 484 / 44 / 140 (the bottom is a
floor: it grows with the caption), `cross_platform` 270 / 672 / 65 / 140 → a
875 × 978 box. `zone_for(channels)`: one family → its zone; more than one, or
a channel with no zone of its own (YouTube) → `cross_platform`.

Carousel: every slide the first slide's ratio, ≤ 20 slides, ≤ 30 MB an image
(`carousel_problems`). Our own cap stays `post_brief.MAX_ASSETS` (10).

## Who reads it

- **The post contract** (`core/post_brief`): `aspect_ratio` is DERIVED —
  `for_post(post_format, placement)`: a reel or a story (`placement: "story"`,
  a post-level field) is vertical, everything else the feed default. Whatever
  the agent writes in `aspect_ratio` is not read; the action hint no longer
  offers it. A vertical post's assets carry `safe_zone`.
- **The renderer** (`core/slide_spec`, `core/slide_renderer`): canvases come
  from the table. On a vertical canvas the spec carries `safe_zone` (set by
  the system, in the schema) and every percentage — text boxes and the
  character — is measured inside the SAFE BOX (`layout_frame`); margins are
  never smaller than the zone's reserve; the background still fills the whole
  canvas. A feed canvas uses the whole frame. `core/copy_budget` measures in
  the same frame.
- **Generation** (`core/media_gen_service`, `agents/media_agent`): the image
  model has no 4:5, so it is asked for 3:4 at **1080p** (the default was 720p)
  and `core/image_fit` crops from the centre. What came back is measured and
  logged (`[media_gen] … returned WxH; after the crop …`), and
  `social_formats.report` alerts when the final image is narrower than 1080 px
  (`low_resolution` flag on the Social card). The free-brief path makes a feed
  image at 4:5 now (it was a square).
- **The Social tab**: `GET /api/client/social` returns `formats`
  (`social_formats.table()`); the page keeps no copy of the numbers.

## Not verified live

What `higgsfield-ai/soul/standard` really returns for 3:4 at 1080p has not
been observed: the first production run logs it. If the short side after the
crop is under 1080 px the alert fires and names the size — that is the bug
report this module was asked to raise, not a silent ship. The presenter clip
also defaults to 1080p now (`MEDIA_PRESENTER_RESOLUTION` still overrides);
its credit cost at 1080p has not been measured.

## Preview (the Social tab, 2026-11-25)

The Social tab shows each post the way the platform will (`dashboard/client/index.html`, `socialStage`):

- A phone frame at the format's real ratio. The size comes from `frame` on every queue item
  (`core/social_preview.frame`, read from this table) — the page keeps no number of its own
  (`tests/test_social_preview.py` fails on a size literal in that script).
- The profile row carries the client's ACTUAL account name (`social_preview.profile`: Instagram username,
  Facebook Page name, TikTok display name — read live with the client's own connection, kept in memory for
  6 hours, never stored). A channel that is not connected shows the business name and says so; a handle is
  never invented.
- Every slide of one post is ONE card: a swipeable carousel with dots, in slide order, approved together;
  a send-back is about the slide in view.
- The caption is folded behind "עוד" where that platform folds it (`CAPTION_FOLD`), hashtags and mentions
  are styled as links, line breaks are kept. The frame is LTR inside the RTL page; the post's own text
  takes its own direction.
- A vertical post has a toggle that draws the safe-zone bands of its zone over the frame.
- The old narrow strip: the card was a positional grid `132px 1fr` built for thumbnail + body. A card
  without a thumbnail (a design, a copy card) put its whole body in the 132 px track. The card is now two
  NAMED areas (stage / body), stacked on a phone.