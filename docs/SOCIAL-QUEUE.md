# The Social tab: one approval gate for social media (2026-11-22)

`core/social_queue.py`, client tab "סושיאל" (next to "מדיה"), admin Control Room mirror.

## What it is

Everything heading to a social channel waits here for the business owner's final yes, whoever made it:

| Source | Enters the queue when | Producer |
|---|---|---|
| Weekly plan (`core/content_team.produce`) | an image/video item for instagram / facebook / tiktok is produced (`_queue_for_approval`; the plan's day becomes the planned date; the caption is the plan's) | ליאור |
| A chat request (`produce_requested_content`) | each made piece that names a social channel (website pieces stay out) | ליאור |
| A media-library video for TikTok / YouTube | the client (or an agent) asks to send a file that is not approved yet: the send card is NOT opened, the file is queued and the reply says it waits in the Social tab | ליאור |
| A fix after a rejection | the fixed piece replaces the rejected item (`replaces` / `fixed_by`) | the item's producer |

Statuses: `waiting` → `approved` → `published`, or `rejected`.

Per item the owner can approve, edit the caption, change the channels or date, reject with a reason, and download.
Several items can be approved together in ONE approval card (the tab's dialog, or the chat card
`approve_social_items`). Editing an APPROVED item puts it back to `waiting`, because what was approved changed.

## The gate

`social_queue.check(client_id, channel, file_id | item_id)` returns the approved item or raises `GateClosed`.
Every function that sends anything to a social channel calls it BEFORE any platform call:

| Publisher | Callers | On a closed gate |
|---|---|---|
| `tiktok_content_agent.begin_upload` | the `send_tiktok_to_inbox` card's durable job, `send_tiktok_batch`, the owner-approved big file, the admin `POST /api/tiktok-content/publish` (via `publish()`) | `UploadRefused("not_approved")` → the card's plain line |
| `youtube_content_agent.publish` | `publish_youtube_from_media`, the admin `POST /api/youtube-content/publish`, `verification_demo.upload_test_video` | `{"not_approved": True}`; the demo queues the test video and tells staff to approve it from the internal account |
| `meta_content_agent.publish` | the admin `POST /api/meta-content/publish` | requires `queue_item` (an approved item id) in the spec |

There is no override parameter. An unreadable queue keeps the gate closed. A channel the item wasn't approved
for, another client's item, a waiting / rejected / already-published item: all closed.
`tests/test_social_queue.py` proves each case and scans the publishers' source for the call.

**Before this change** every one of those paths bypassed any owner approval queue. The three admin-key routes
(Meta / TikTok / YouTube) published with no owner approval at all. The client's send cards needed a click, but
not on a reviewed item. The verification demo uploaded without the client.

## Approved ≠ publish now

`delivery()` derives each channel's state from `content_team.channel_modes`:

- `scheduled`: we publish automatically on the planned date (a channel in `auto` mode).
- `one_tap`: ready to publish in one tap. The file and caption are prepared, and the owner publishes it.
- `not_connected`: download it and publish it yourself, or connect the channel.
- `drafts` / `private`: we sent it to TikTok drafts / uploaded it as private on YouTube, and the owner's tap is still needed.
- `done`: published.

Today `content_team.AUTO_PUBLISH` is all False (Meta is not verified, TikTok's review is not done), so **every
channel is a tap**. No scheduled-publish job is built: CLAUDE.md forbids adding a publish call to content_team or
flipping AUTO_PUBLISH without the owner and a tested path. When a channel opens, it shows `scheduled`, and the
date job is the one piece left to build (it must go through `check()`).

The row's "I published it ✓" (`/published`) closes a one-tap item. A real Meta publish marks the channel `done`.

## Reject → the producer

`reject()` needs a reason (2-400 chars). `send_back()` then:

1. posts a line in the PRODUCER's chat thread (`dashboard_chat:media` / `:meta`) quoting the reason, and
2. proposes `produce_requested_content` there (one card) with the original brief + the reason, the same caption,
   channels and date, and `replaces` = the item.

When the fixed piece is made, it enters the queue as a new `waiting` item linked to the old one. If no card can
be opened, one admin alert is sent.

## Routes

Client (session; staff "view as client" is read-only, and only the GET is in `admin_view.READ_ENDPOINTS`):

- `GET /api/client/social?offset=&limit=&status=&lang=`: a page of rows (default 6, "show more"), counts, the empty line.
- `POST /api/client/social/approve` `{ids: [...]}`: one or several (≤20).
- `POST /api/client/social/{id}/edit` `{caption?, channels?, planned_date?}`
- `POST /api/client/social/{id}/reject` `{reason}`
- `POST /api/client/social/{id}/published`

Preview / download reuse the media-drive routes (`thumb`, `play`, `file`). The files stay in the client's Drive
folder; an item holds only the Drive file id. Videos open in Drive (never downloaded through us, docs/MEDIA-TAB.md).

Admin (read-only):

- `GET /api/admin/control-room/{client_id}/social`: the client view's panel.
- `GET /api/admin/social-queue`: every client's open queue (waiting / approved / waiting for the owner's tap, next date), shown in the Control Room list.

`/api/dashboard` carries `social_waiting` for the nav badge.

## Order and gradual release

The order is: waiting, then approved, then rejected, then published. Inside a group, the nearest planned date
comes first, with overdue items on top. The tab shows 6 rows with a "show more" button.

## Storage

app_settings `social_queue:<client_id>` = `{"items": [...]}`. It is capped at 300 items, and only finished
(published / rejected) items are pruned. Client deletion, the deletion guard and the settings list cover the key.
**No migration.**

## Not built / not tested live

- Automatic publishing on the planned date (see above).
- No real browser, model, client or platform delivery was used in the tests.

## Enlarged view (2026-11-24)

Clicking a picture in the tab (a queue item's image, or a design's slide) opens the shared lightbox
(`/assets/media-viewer.js`, items with `src`). It browses the same context: the slides of that carousel
(loaded queue items sharing `post_ref.set_id`, in `post_ref.n` order; videos are not in it) or the slides
of that design. Arrows, keyboard (RTL visual order: ← next), swipe, "3 / 5"; ✕, the dark area and Esc
close. The card keeps its thumbnail (`/api/client/media-drive/thumb/<id>`); the full image
(`/api/client/media-drive/file/<id>?inline=1`) loads only in the overlay, plus the next and previous one;
the thumbnail is the fallback. Tests: `tests/test_social_lightbox.py`.
