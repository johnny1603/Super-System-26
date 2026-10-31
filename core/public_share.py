"""A client's file is public only while a vendor's server fetches it.

Some vendors take a media file only as a public URL (the avatar tool's
twin-creation call, an avatar video's audio track, a character image). A
Drive file made public with drive_service.make_file_public stays public
until someone removes the share, and for the client's own face and voice
that must never be "forever". So every such share goes through:

    with public_share.briefly_public(client_id, AGENT_NAME, "avatar creation") as share:
        vendor_call(share.url(training_id), share.url(consent_id))

- The file is recorded BEFORE it is made public, so a share that half
  happened is closed too.
- On leaving the block — success, refusal or exception alike — every file's
  public access is removed and then READ BACK (drive_service.revoke_public).
  A failed or unverified close is retried once.
- Still public after the retry → an admin alert naming the file, which says
  to remove the share by hand. The vendor job's own result is unaffected.

The close runs in the client's Drive scope (core/drive_storage), wherever
their files live.
"""
import contextlib
import time

from core import drive_storage

RETRY_PAUSE_SECONDS = 2


class _Shares:
    def __init__(self):
        self.file_ids = []

    def url(self, file_id: str) -> str:
        from core import drive_service as drive
        self.file_ids.append(file_id)
        return drive.make_file_public(file_id)


def _close_one(file_id: str, _sleep=time.sleep) -> bool:
    from core import drive_service as drive
    for attempt in range(2):
        try:
            if drive.revoke_public(file_id):
                return True
        except Exception as e:
            print(f"[public_share] close of {file_id} failed (attempt {attempt + 1}): {type(e).__name__}")
        if attempt == 0:
            _sleep(RETRY_PAUSE_SECONDS)
    return False


@drive_storage.scoped
def close(client_id: int, file_ids: list, agent: str, why: str, _sleep=time.sleep) -> list:
    """Remove public access from each file, verified. Returns the ids that are
    STILL public (and alerts the team about them)."""
    from core.agent_base import agent_alert, log_step
    still_public = [f for f in dict.fromkeys(file_ids) if not _close_one(f, _sleep)]
    if still_public:
        agent_alert(agent, [f"client {client_id}: {why}: the client's file(s) {', '.join(still_public)} "
                            f"are STILL publicly reachable after two attempts to close them - remove the "
                            f"'anyone with the link' share by hand in Drive now"])
    else:
        log_step(agent, "public_share_closed", f"client {client_id}: {why}: {len(file_ids)} file(s) closed")
    return still_public


@contextlib.contextmanager
def briefly_public(client_id: int, agent: str, why: str):
    shares = _Shares()
    try:
        with drive_storage.scope(client_id):
            yield shares
    finally:
        if shares.file_ids:
            close(client_id, shares.file_ids, agent, why)
