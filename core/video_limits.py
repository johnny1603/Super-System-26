"""THE size limit for a Drive → YouTube upload — one constant, read by every
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


def fits(size_bytes) -> bool:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return False
    return 0 < size <= VIDEO_UPLOAD_MAX_BYTES
