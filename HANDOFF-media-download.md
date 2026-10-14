# HANDOFF — Media tab: image preview + direct download (2026-10-14)

Builds on the Drive mirror (7e14256): same listing, same folder (the client's
root from our `media_folder_created` record), same service account.

## Investigation

1. **Drive `thumbnailLink` at a larger size — yes, and no proxy needed.**
   Tested on the live mirror listing for client 27: the link
   (`lh3.googleusercontent.com/drive-storage/…=s220`) fetched with NO cookies
   and NO auth returned `200 image/jpeg` (11.5 KB); swapping the suffix to
   `=s1600` returned `200 image/jpeg`, 195 KB, a real larger rendition. So the
   lightbox loads Google's renderer directly. Caveats: the link is short-lived
   (hours) and is a bearer URL (anyone holding it can view the image until it
   expires). The listing cache is 3 min, so links are fresh. If one has
   expired (a page left open for hours), the lightbox falls back once to
   `/file/{id}?inline=1` (raster only, ≤25 MB, through our server).
2. **Cloud Run response limits.** The quotas page says the 32 MiB response
   cap applies to HTTP/1 responses *not* using `Transfer-Encoding: chunked`
   or streaming. A streamed response is exempt, so the endpoint deliberately
   sends **no Content-Length**: the browser shows no percentage, but there is
   no size ceiling. (HTTP/2 end-to-end lifts the 32 MiB *request* cap; it is
   not needed for downloads.) The real bound is the **request timeout**:
   max 60 min, and this service plus the `app.uallak.com` proxy both run with
   `--timeout=300`. A download must finish in 5 minutes: 100 MB needs about
   2.7 Mbit/s sustained, and a 1 GB video needs about 27 Mbit/s. Egress is
   also paid twice (me-west1 → proxy region, then proxy → internet). Hence
   the rules in the change request below: videos never go through us, and
   images/documents only up to a cap.
3. **Agent-created files and files added in Drive share one endpoint.** The
   download takes a Drive file id and authorises it by walking Drive's own
   `parents` up to the client's root, never by trusting a `client_activity`
   row. Agent files live in the same `client-{id}/…` tree, so they pass. A
   stale row whose file was moved or deleted gets "no longer exists". The
   index grid's download button sends `file_id` from the row, and the server
   decides file vs. Google-Doc export.

## Change request (second commit): videos Drive-only, images/documents capped

- **Videos: no inline player and no download through our server, for any
  role (admin included).** The card shows Google's thumbnail (loaded straight
  from Google) with "Open in Drive" and the line "Videos open in Drive…" (5
  languages). The lightbox's video-player path was removed; video cards
  don't open the lightbox at all.
- **Images and documents (PDF, Office, Google exports): through us only up to
  the cap.** Above it, the card shows the thumbnail with "Open in Drive" and
  the calm "large file" explanation, and the lightbox doesn't open.
- **The setting:** `media_download_max_mb` was **reused** rather than adding a
  second one. With videos never proxied, it only ever governs images and
  documents, so a second setting would be a duplicate knob. The default
  dropped from 100 to 20 MB (ceiling 200) and it was relabelled in Settings. No
  value was ever saved under the old default (that commit was never
  deployed), so 20 applies from the first deploy.
- **Enforced in `drive_download.plan()`, not just the UI.** A video mime type,
  a file over the cap, a non-raster `?inline=1` and a Google Form/Site all
  raise `OpenInDrive`, and only AFTER the ownership walk, so the Drive link is
  always the client's own file. That is not an error: `?check=1` answers
  `200 {"ok": false, "open_in_drive": true, "reason": "video|too_large|drive_only",
  "web_view_link", "max_mb"}`, and a direct (hand-crafted) request is
  `303`-redirected to the file in Drive. Same code path for the admin route,
  with no bypass. Google exports are bounded by min(cap, Drive's 10 MB).
- A stream that has to stop mid-way (export grew past the cap, Drive dropped)
  now ABORTS the connection instead of ending cleanly, so the browser shows a
  failed download rather than saving a truncated file as complete.

## What was built (first commit)

- `core/drive_download.py`: `plan()` (all checks, no body), `open_stream()`
  (alt=media / files.export, 256 KB chunks, nothing buffered), name
  sanitising (NFC, control + bidi-override characters stripped, RFC 5987
  `filename*`, ASCII fallback `download.ext`), in-memory per-client rate limit
  (60 requests / 10 min, 3 concurrent), and bytes sent logged to
  `client_costs` as `drive_download_egress` (`details.bytes`).
- Endpoints: `GET /api/client/media-drive/file/{id}` (session) and
  `GET /api/admin/control-room/{client_id}/media-drive/file/{id}` (admin).
  `?check=1` returns JSON (the page checks first, so a refusal is a sentence,
  not a broken file in the downloads bar). `?inline=1` is for raster images only.
  `?format=office` exports Google files as docx/xlsx/pptx (default PDF).
- Safety: inline is served ONLY for jpeg/png/webp/gif. Everything else is an
  attachment, and anything outside a small safe list goes out as
  `application/octet-stream`. Every response carries `nosniff` and
  `CSP: sandbox; default-src 'none'`. Folders, shortcuts, the media root and
  other clients' folders are refused (404, same answer as "gone").
- `dashboard/assets/media-viewer.js`: the lightbox (next/prev, Esc,
  click-outside, swipe, RTL-aware arrows, focus trap) and the check-then-
  download helper, shared by both dashboards. It builds DOM only with
  `textContent`/`setAttribute`.
- Client Media tab: cards have Open in Drive + Download, with "Large file —
  download from Drive" above the cap (videos: see the change request). Messages go to a status line in the tab
  (never into chat). Strings exist in he/en/fr/ar/ru.
- Control Room: the same lightbox + download per client. Settings: "גודל
  קובץ מרבי (MB)". The overview and client drawer show the month's download
  traffic in MB and estimated ₪ (amounts per download are too small to survive
  the 2-decimal category rounding, so bytes are summed separately).

## "Download all" as a ZIP — not built, recommendation: don't (yet)

Building the ZIP on Cloud Run means one request reads every file from Drive
and writes a ZIP stream. Streaming ZIP (stored, no compression; media is
already compressed) avoids holding files in memory, but:
- one request = the whole folder, so the 300 s timeout caps it at roughly
  the same ~100 MB as a single file. A 20-video folder will be cut off mid-way,
  leaving a corrupt ZIP the client can't open.
- the CPU/thread is held for the whole duration, egress is paid twice, and
  the rate limit can't meaningfully apply to one giant request;
- buffering instead (to know Content-Length / to retry) would put whole
  folders in instance memory, the failure mode this design avoids.
Google Drive already offers this for free: in the shared folder, select all →
Download gives a server-built ZIP of any size. Recommendation: a "Download the
whole folder from Drive" link (folder `webViewLink`) instead of our own ZIP. If
a real one is ever needed, build it as an async job (zip in Cloud Run Jobs →
upload to the client's `exports/` folder → link), never in the request path.

## Verification status

See the commit report: lightbox, RTL arrows, Esc/click-outside, escaping of a
name containing markup, the s1600 thumbnail and the Drive /preview embed were
exercised in Chrome against the live admin page with the new viewer injected
(before deploy). The download endpoint itself needs the deploy.
