"""Test helper — import it ONLY from tests of the send MECHANICS (TikTok chunks,
YouTube streaming, the send cards' own rules): the Social-tab gate
(core/social_queue.check) answers as if the owner had approved the file for
that channel, so those tests keep testing what they were written for.

Each test file runs in its own process (scripts/run_tests.py), so this never
reaches tests/test_social_queue.py, which tests the gate itself and proves it
cannot be bypassed. Never import this from production code.
"""
from core import social_queue


def _approved(client_id, channel, file_id="", item_id=""):
    return {"id": item_id or "testapproved", "file_id": file_id, "channels": [channel],
            "status": "approved", "caption": "", "delivered": {}}


social_queue.check = _approved
social_queue.mark_delivered = lambda *a, **k: None
social_queue.add_safely = lambda *a, **k: {}
