"""THE size limits for Drive → YouTube and Drive → TikTok-inbox uploads
(TIKTOK_INBOX_MAX_MB below). The YouTube one is one constant, read by every
place that decides or enforces it, so they can never disagree:

  core/client_state ........ media_folder.videos[].uploadable, upload_max_mb
  core/agent_actions ....... publish_youtube_from_media's drive_file_id validator
  core/verification_demo ... the Control Room demo's video pick
  youtube_content_agent .... publish() refuses anything bigger (the root guard,
                             whatever path called it)

Why 512 MB: the upload STREAMS (core/youtube_service.upload_video_stream) —
Drive is read by range, one CHUNK at a time, straight into YouTube's
resumable session — so memory no longer grows with the file. What bounds
the size now is time: a click on a chat card waits for the upload inside one
request, and the app sits behind a ~300 s request timeout (proxy read
290 s). 512 MB at Google-to-Google speeds is well inside that; a bigger cap
needs the upload moved to a background job first.

Stdlib only on purpose: tests and lightweight readers import it freely.
"""
VIDEO_UPLOAD_MAX_MB = 512
VIDEO_UPLOAD_MAX_BYTES = VIDEO_UPLOAD_MAX_MB * 1024 * 1024

# Resumable-upload chunk: YouTube requires multiples of 256 KiB for every
# chunk but the last; 8 MiB is Google's recommended size and the most bytes
# the upload ever holds in memory at once.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


# YouTube's own metadata limits (videos.insert snippet). The description limit
# is 5,000 bytes; we stop at 4,900 characters of mostly-ASCII-or-Hebrew text
# and youtube_service re-cuts on bytes as the last guard.
TITLE_MAX = 100
DESCRIPTION_MAX = 4900
DESCRIPTION_MAX_BYTES = 5000
TAGS_TOTAL_MAX = 500
TAGS_MAX = 15


def tags_length(tags: list) -> int:
    """Characters YouTube counts against the 500 tag limit: each tag, plus the
    comma between tags, plus 2 quote marks for a tag that contains a space."""
    tags = [str(t) for t in tags or []]
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def fits(size_bytes) -> bool:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return 0 < size <= VIDEO_UPLOAD_MAX_BYTES


# Drive → TikTok inbox (tiktok_content_agent.publish), STREAMED in chunks
# (2026-11-09). TikTok's Media Transfer Guide (FILE_UPLOAD): each chunk 5-64
# MB except the last, which may be bigger (up to 128 MB) and absorbs the
# remainder; total_chunk_count = floor(video_size / chunk_size), 1-1000;
# a file under 5 MB goes whole as one chunk; chunks sequential, each PUT with
# its own Content-Range; 206 = more to come, 201 = all received; the file may
# be up to 4 GB and the upload_url lives 1 hour. Our cap is 512 MB — the same
# as the YouTube stream: the click's run deadline (10 min for this card, Drive
# read + TikTok upload + TikTok's processing) is what bounds it, not TikTok.
# Memory: one chunk at a time (+ the MP4 header for the format check). Read by
# client_state (media_folder.videos[].tiktok_uploadable), the
# send_tiktok_to_inbox validator, and publish() as the root guard.
TIKTOK_INBOX_MAX_MB = 512
TIKTOK_INBOX_MAX_BYTES = TIKTOK_INBOX_MAX_MB * 1024 * 1024
TIKTOK_CHUNK_BYTES = 16 * 1024 * 1024        # within 5-64 MB
TIKTOK_MIN_CHUNK_BYTES = 5 * 1024 * 1024
TIKTOK_MAX_CHUNKS = 1000


def tiktok_chunk_plan(size_bytes: int) -> list:
    """[(start, length), ...] per TikTok's rules: under one chunk size → one
    chunk (the whole file, chunk_size = size); otherwise chunks of
    TIKTOK_CHUNK_BYTES and the LAST one takes the remainder (it is then
    between 1x and 2x the chunk size, inside TikTok's 128 MB final limit)."""
    size = int(size_bytes or 0)
    if size <= 0:
        return []
    if size < TIKTOK_CHUNK_BYTES:
        return [(0, size)]
    count = size // TIKTOK_CHUNK_BYTES
    plan = [(i * TIKTOK_CHUNK_BYTES, TIKTOK_CHUNK_BYTES) for i in range(count - 1)]
    last_start = (count - 1) * TIKTOK_CHUNK_BYTES
    return plan + [(last_start, size - last_start)]


def tiktok_chunk_size(size_bytes: int) -> int:
    """The init call's chunk_size for this file (see tiktok_chunk_plan)."""
    size = int(size_bytes or 0)
    return size if size < TIKTOK_CHUNK_BYTES else TIKTOK_CHUNK_BYTES


def fits_tiktok(size_bytes) -> bool:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return 0 < size <= TIKTOK_INBOX_MAX_BYTES
