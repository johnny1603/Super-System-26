"""Google verification ROUND 2 switches (docs/GOOGLE-ROUND2.md). All OFF by
default: while off, the round-2 scopes are never requested, their OAuth
routes answer 404, their cards and chat actions are never offered, and no
round-2 API is called. Round 1's scope lists (sign-in, Ads, YouTube, Tag
Manager) do not depend on these at all.

Read from the environment at call time (no restart-time caching). The
operator (core/operator_flags) can switch any of these OFF as a kill switch —
never ON: an ON override is refused and ignored, because ON requests Google
scopes that only the owner can get approved.
"""
from core.operator_flags import enabled as _enabled


def gmail_send_enabled() -> bool:
    return _enabled("GMAIL_SEND_ENABLED")


def search_console_enabled() -> bool:
    return _enabled("SEARCH_CONSOLE_ENABLED")


def analytics_enabled() -> bool:
    """analytics.readonly — GA4 site traffic (core/analytics_service)."""
    return _enabled("ANALYTICS_ENABLED")


def drive_file_enabled() -> bool:
    """drive.file — the client's files in their OWN Google Drive
    (core/drive_file_service, core/drive_storage)."""
    return _enabled("DRIVE_FILE_ENABLED")
