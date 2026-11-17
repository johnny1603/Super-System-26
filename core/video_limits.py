"""THE size limits for Drive → YouTube uploads, and the Drive → TikTok-inbox
chunking + owner-approval threshold (below). The YouTube one is one constant, read by every
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


# Drive → TikTok inbox: NO size cap of ours (owner's policy, 2026-11-10).
# - Up to the admin setting `tiktok_auto_upload_max_mb` (default
#   TIKTOK_AUTO_UPLOAD_MAX_MB_DEFAULT = 1536 = 1.5 GB): the client's own card
#   click is all it takes.
# - Above it: never refused, the client is never asked — ONE approval request
#   goes to the owner (core/tiktok_uploads: admin bell + email); his Approve is
#   final, with no further cap.
# TikTok's own limits (4 GB, 10 min, ...) are NOT checked here: a rejection
# at runtime is handled on our side (an alert), never pushed to the client.
#
# The upload STREAMS in chunks as a durable job (core/tiktok_uploads), using
# TikTok's FILE_UPLOAD rules (Content Posting API → Media Transfer Guide,
# developers.tiktok.com/doc/content-posting-api-media-transfer-guide, read 2026-11-17):
# - "Each chunk must be at least 5 MB but no greater than 64 MB, except for the
#   final chunk, which can be greater than chunk_size (up to 128 MB) to
#   accommodate any trailing bytes";
# - total_chunk_count "should be equal to video_size divided by chunk_size,
#   rounded down to the nearest integer"; between 1 and 1000 chunks;
# - a file under 5 MB goes whole, chunk_size = the file's full size;
# - "Videos with a total size greater than 64 MB must be uploaded in multiple chunks";
# - chunks are sent sequentially, each PUT with its own Content-Range.
# PRODUCTION (client 27, 2026-11-17): an 18 MB file was initialised with
# chunk_size 16 MiB and total_chunk_count 1 → "400 invalid_params: The chunk
# size is invalid". So: ONE chunk ⇔ chunk_size == video_size. Every file TikTok
# lets go as one chunk (up to 64 MB) goes whole; above it, 16 MiB chunks, the
# last one absorbing the remainder, always at least 2 chunks.
# "MB" is read as 10^6 bytes on the limits (the stricter reading of both).
#
# Memory: one chunk at a time (at most ~64 MB for a whole small file).
TIKTOK_AUTO_UPLOAD_MAX_MB_DEFAULT = 1536
TIKTOK_CHUNK_BYTES = 16 * 1024 * 1024        # the multi-chunk size, within 5-64 MB
TIKTOK_MIN_CHUNK_BYTES = 5 * 1000 * 1000
TIKTOK_MAX_CHUNK_BYTES = 64 * 1000 * 1000
TIKTOK_SINGLE_CHUNK_MAX_BYTES = TIKTOK_MAX_CHUNK_BYTES   # the largest file sent as ONE chunk
TIKTOK_MAX_FINAL_CHUNK_BYTES = 128 * 1000 * 1000
TIKTOK_MAX_CHUNKS = 1000
_MiB = 1024 * 1024


def tiktok_chunk_size(size_bytes: int) -> int:
    """The init call's chunk_size: the WHOLE file up to 64 MB (one chunk, so
    chunk_size == video_size); above it 16 MiB, grown (whole MiB, up to 64 MB)
    only when 16 MiB would need more than 1000 chunks (files over ~16 GB)."""
    size = int(size_bytes or 0)
    if size <= TIKTOK_SINGLE_CHUNK_MAX_BYTES:
        return size
    chunk = TIKTOK_CHUNK_BYTES
    if size // chunk > TIKTOK_MAX_CHUNKS:
        chunk = min(-(-size // TIKTOK_MAX_CHUNKS // _MiB) * _MiB + _MiB, TIKTOK_MAX_CHUNK_BYTES)
    return chunk


def tiktok_chunk_plan(size_bytes: int, chunk_size: int = None) -> list:
    """[(start, length), ...] per TikTok's rules: chunk_size >= the file → ONE
    chunk (the whole file); otherwise floor(size / chunk_size) chunks of
    `chunk_size`, the LAST one taking the remainder (between 1x and 2x)."""
    size = int(size_bytes or 0)
    if size <= 0:
        return []
    chunk = int(chunk_size or tiktok_chunk_size(size))
    if size <= chunk:
        return [(0, size)]
    count = max(size // chunk, 1)
    plan = [(i * chunk, chunk) for i in range(count - 1)]
    last_start = (count - 1) * chunk
    return plan + [(last_start, size - last_start)]


def tiktok_init_params(size_bytes: int) -> dict:
    """{video_size, chunk_size, total_chunk_count} for the init call, checked
    against every rule above — ValueError instead of a call TikTok refuses."""
    size = int(size_bytes or 0)
    chunk = tiktok_chunk_size(size)
    plan = tiktok_chunk_plan(size, chunk)
    count = len(plan)
    if size <= 0 or not plan:
        raise ValueError("empty file")
    if count == 1 and chunk != size:
        raise ValueError("one chunk must be the whole file")
    if count > 1:
        if not (TIKTOK_MIN_CHUNK_BYTES <= chunk <= TIKTOK_MAX_CHUNK_BYTES):
            raise ValueError("chunk_size out of range")
        if count != size // chunk or count > TIKTOK_MAX_CHUNKS:
            raise ValueError("total_chunk_count does not match")
        if plan[-1][1] > TIKTOK_MAX_FINAL_CHUNK_BYTES:
            raise ValueError("final chunk too large")
    elif size > TIKTOK_SINGLE_CHUNK_MAX_BYTES:
        raise ValueError("a file over 64 MB must go in several chunks")
    return {"video_size": size, "chunk_size": chunk, "total_chunk_count": count}


def tiktok_auto_upload_max_mb() -> int:
    """The owner's threshold (admin setting `tiktok_auto_upload_max_mb`).
    Unreadable → the default; never below 1."""
    try:
        from core import admin_service
        value = admin_service.get_setting("tiktok_auto_upload_max_mb", TIKTOK_AUTO_UPLOAD_MAX_MB_DEFAULT)
        return max(int(float(value)), 1)
    except Exception:
        return TIKTOK_AUTO_UPLOAD_MAX_MB_DEFAULT


def tiktok_needs_owner_approval(size_bytes) -> bool:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return size > tiktok_auto_upload_max_mb() * _MiB


def tiktok_sendable(size_bytes) -> bool:
    """Any real file can go to TikTok — there is no size cap of ours."""
    try:
        return int(size_bytes or 0) > 0
    except (TypeError, ValueError):
        return False
