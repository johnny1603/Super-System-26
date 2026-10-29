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


# Drive → TikTok inbox (tiktok_content_agent.publish): ONE FILE_UPLOAD chunk,
# read whole into memory. TikTok takes a single-chunk upload only up to 64 MB
# (bigger needs multi-chunk, not built — see the tiktok skill). Read by
# client_state (media_folder.videos[].tiktok_uploadable), the
# send_tiktok_to_inbox validator, and publish() as the root guard.
TIKTOK_INBOX_MAX_MB = 64
TIKTOK_INBOX_MAX_BYTES = TIKTOK_INBOX_MAX_MB * 1024 * 1024


def fits_tiktok(size_bytes) -> bool:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return 0 < size <= TIKTOK_INBOX_MAX_BYTES
