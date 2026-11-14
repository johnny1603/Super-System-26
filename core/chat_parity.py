"""Chat parity — everything a client can do in the dashboard, the manager
chat can do too (2026-11-02, docs/CHAT_PARITY.md).

Every session-gated client route in core/api_server.py has ONE entry here,
saying how the chat covers it:

  ("chat", "<action>[,<action>…]")  an agent_actions card does it (one click)
  ("field", "<output field>")       a chat output field does it
                                    (upgrade_request / a team topic)
  ("read", "<client_state section>[,…]")  the chat answers it from account_state
  ("not_chat", "<why>")             deliberately NOT in the chat — the reason
                                    is the allowlist entry (a password, a key,
                                    a legal consent, the chat's own plumbing)
  ("gap", "P1|P2|P3: <what to build>")  missing today; the priority says when

tests/test_chat_parity.py fails when a new client route appears without an
entry here, when an entry names an action or section that doesn't exist, or
when a not_chat entry has no real reason. Adding a dashboard button means
adding its chat path (or its honest reason) in the same change.
"""

CHAT, FIELD, READ, NOT_CHAT, GAP = "chat", "field", "read", "not_chat", "gap"

_PLUMBING = "the chat's own plumbing (this IS the chat or its card), not a client capability"
_PAGE_LOAD = "the dashboard page's own data load; the chat has the same facts in account_state"
_ONBOARDING = "a first-login / onboarding screen event the dashboard sends by itself, not a client request"
_PICKER = "the account picker Google returns to after the client's own consent; it only exists on that screen"
_KEY = "the client's own key or password is pasted ONLY in its card, never in a chat (the chat offers that card)"

PARITY = {
    # ── Account, dashboard, performance ──────────────────────────────────────
    "GET /api/client/account": (NOT_CHAT, _PAGE_LOAD),
    "POST /api/client/password": (NOT_CHAT, "a password is typed only on the password screen, never in a chat"),
    "GET /api/dashboard": (READ, "channels,google_campaigns,meta_campaigns,budget,leads,approvals"),
    "GET /api/client/performance": (READ, "google_campaigns,meta_campaigns,budget,leads,website_traffic"),
    "POST /api/client/profile": (GAP, "P2: update_business_profile card (business details the client corrects in words)"),
    "GET /api/client/manager": (NOT_CHAT, "the manager's name and face ARE the chat (manager_identity.prompt_line)"),
    "POST /api/client/manager": (CHAT, "change_manager"),
    # ── Connection help + the chat itself ────────────────────────────────────
    "GET /api/client/connection-help": (CHAT, "request_connection_help"),
    "GET /api/client/connection-help/{connection_id}": (CHAT, "request_connection_help"),
    "POST /api/client/connection-help/{connection_id}/assist": (CHAT, "request_connection_help"),
    "POST /api/client/connect-chat": (NOT_CHAT, _PLUMBING),
    "POST /api/client-chat": (NOT_CHAT, _PLUMBING),
    "POST /api/client-chat/prewarm": (NOT_CHAT, _PLUMBING),
    "GET /api/client-chat/pace": (NOT_CHAT, _PLUMBING),
    "GET /api/client-chat/history": (NOT_CHAT, _PLUMBING),
    "GET /api/client-chat/unread": (NOT_CHAT, _PLUMBING),
    "POST /api/client-chat/new": (NOT_CHAT, _PLUMBING),
    "POST /api/client/chat-upload": (NOT_CHAT, _PLUMBING),
    "POST /api/client/chat-upload/status": (NOT_CHAT, _PLUMBING),
    "GET /api/client/actions": (NOT_CHAT, _PLUMBING),
    "GET /api/client/actions/{proposal_id}": (NOT_CHAT, _PLUMBING),
    "POST /api/client/actions/{proposal_id}/decide": (NOT_CHAT, _PLUMBING),
    # ── Package, upgrade, billing ────────────────────────────────────────────
    "GET /api/client/upgrade-options": (FIELD, "upgrade_request"),
    "POST /api/client/upgrade": (NOT_CHAT, "the purchase itself is confirmed on the billing screen with the payment "
                                           "provider; the chat builds the priced offer (upgrade_request)"),
    "GET /api/client/billing": (GAP, "P2: a billing section in account_state (plan, next charge, last invoice)"),
    "GET /api/client/external-costs": (GAP, "P3: external tool costs in account_state"),
    # ── Onboarding / engagement events ───────────────────────────────────────
    "POST /api/client/interview": (NOT_CHAT, _ONBOARDING),
    "GET /api/client/interview/state": (NOT_CHAT, _ONBOARDING),
    "GET /api/client/connection-checklist": (READ, "channels"),
    "POST /api/client/connection-nudge": (NOT_CHAT, _ONBOARDING),
    "POST /api/client/login-moment": (NOT_CHAT, _ONBOARDING),
    "POST /api/client/feature-announcement": (NOT_CHAT, _ONBOARDING),
    "GET /api/client/journey": (NOT_CHAT, _ONBOARDING),
    "POST /api/client/tour-completed": (NOT_CHAT, _ONBOARDING),
    "POST /api/client/feedback": (GAP, "P3: store feedback the client gives in the chat (engagement_agent.store_feedback)"),
    # ── Account lifecycle ────────────────────────────────────────────────────
    "POST /api/client/disconnect": (GAP, "P2: disconnect_platform card (revokes one connection; destructive, so its "
                                         "own explicit card)"),
    "GET /api/client/data-export": (NOT_CHAT, "a file download; the chat cannot hand over a file"),
    "POST /api/client/close-account": (NOT_CHAT, "closing the account is irreversible and confirmed on its own screen "
                                                 "and by e-mail; in the chat it is a billing matter for the team"),
    "POST /api/client/transfer-out": (NOT_CHAT, "the transfer is confirmed by e-mail with the data attached; in the "
                                                "chat it is a contract matter for the team"),
    # ── Google connections ───────────────────────────────────────────────────
    "GET /api/oauth/google-ads/start": (CHAT, "connect_google_ads"),
    "GET /api/google-ads/accounts": (NOT_CHAT, _PICKER),
    "POST /api/google-ads/select-account": (NOT_CHAT, _PICKER),
    "GET /api/gtm/connection": (READ, "tracking"),
    "GET /api/gtm/containers": (NOT_CHAT, _PICKER),
    "POST /api/gtm/select-container": (NOT_CHAT, _PICKER),
    "GET /api/oauth/gtm/start": (CHAT, "connect_tag_manager"),
    "GET /api/oauth/youtube/start": (CHAT, "connect_youtube"),
    "GET /api/oauth/gmail/start": (CHAT, "connect_gmail_send"),
    "GET /api/oauth/search-console/start": (CHAT, "connect_search_console"),
    "GET /api/client/search-console/sites": (NOT_CHAT, _PICKER),
    "POST /api/client/search-console/site": (NOT_CHAT, _PICKER),
    "GET /api/oauth/analytics/start": (CHAT, "connect_analytics"),
    "GET /api/client/analytics/properties": (NOT_CHAT, _PICKER),
    "POST /api/client/analytics/property": (NOT_CHAT, _PICKER),
    "GET /api/oauth/google-drive/start": (GAP, "P3: connect_google_drive link card"),
    "GET /api/client/google-drive/status": (READ, "connections"),
    # ── Other platforms ──────────────────────────────────────────────────────
    "GET /api/oauth/meta/start": (CHAT, "connect_meta"),
    "GET /api/oauth/tiktok/start": (CHAT, "connect_tiktok"),
    "GET /api/client/tiktok/profile": (READ, "channels"),
    "POST /api/client/media/send-to-tiktok": (CHAT, "send_tiktok_to_inbox"),
    # ── Website ──────────────────────────────────────────────────────────────
    "POST /api/website/connect": (CHAT, "connect_website"),
    "POST /api/website/authorize/start": (CHAT, "connect_website"),
    "POST /api/client/website/assess": (GAP, "P2: assess_site card (the same assess_existing_site check)"),
    "POST /api/client/website/migrate-request": (GAP, "P2: request_site_migration card (the same request_migration)"),
    "POST /api/client/website/rebuild": (GAP, "P1: build_site card (the same request_self_provision gate)"),
    "POST /api/website/self-provision": (GAP, "P1: build_site card (the same request_self_provision gate)"),
    # ── Creative tools, voice, media ─────────────────────────────────────────
    "POST /api/media/connect": (NOT_CHAT, _KEY),
    "POST /api/avatar/connect": (NOT_CHAT, _KEY),
    "POST /api/avatar/consent": (NOT_CHAT, "a likeness/voice consent is a deliberate tick on its own card (the chat "
                                           "offers that card, never ticks it)"),
    "GET /api/client/tools": (READ, "creative_tools"),
    "POST /api/client/tools/provider": (GAP, "P3: choose_video_provider card"),
    "POST /api/client/tools/{tool}/connect": (CHAT, "connect_creative_tool"),
    "POST /api/client/tools/{tool}/test": (GAP, "P3: test_creative_tool card"),
    "POST /api/client/tools/{tool}/disconnect": (GAP, "P3: disconnect_creative_tool card"),
    "GET /api/client/voice": (READ, "voice"),
    "POST /api/client/voice/upload": (NOT_CHAT, "a recording is uploaded as a file; the chat takes files through its "
                                                "own upload (chat-upload)"),
    "POST /api/client/voice/delete": (GAP, "P2: delete_voice_recording card"),
    "GET /api/client/avatar-footage": (NOT_CHAT, "retired 2026-10-31 (filmed twin); kept only for old open tabs"),
    "POST /api/client/avatar-footage/create": (NOT_CHAT, "retired 2026-10-31; the chat's create_presenter_from_photos "
                                                         "replaced it"),
    "GET /api/client/media-folder": (READ, "media_folder"),
    "GET /api/client/media-drive": (READ, "media_folder"),
    "GET /api/client/media-drive/file/{file_id}": (NOT_CHAT, "streams a file to the browser; not a capability"),
    "GET /api/client/media-drive/thumb/{file_id}": (NOT_CHAT, "streams a thumbnail to the browser; not a capability"),
    "GET /api/client/media-assets": (READ, "media"),
    # Media tab library (2026-11-11): the chat finds files by name with the same matcher
    "GET /api/client/media-library": (READ, "media_folder"),
    "POST /api/client/media-library/seen": (NOT_CHAT, "stamps when the client last opened the Media tab, "
                                                      "for its New badges; UI bookkeeping, nothing to ask for"),
    "GET /api/client/media-drive/play/{file_id}": (NOT_CHAT, "streams a video into the Media tab's player; "
                                                             "the chat links the file instead"),
    "POST /api/client/media/send": (CHAT, "send_tiktok_to_inbox,publish_youtube_from_media"),
    # Add by link (2026-11-11): a pasted Drive link in the chat opens the same copy as a card
    "POST /api/client/media/add-link": (CHAT, "add_media_from_drive_link"),
    "POST /api/client/media/add-link/step": (NOT_CHAT, "the Media tab's progress poll for a running copy; "
                                                       "the chat card reports its own outcome"),
    # ── Leads + CRM ──────────────────────────────────────────────────────────
    "GET /api/client/leads": (READ, "leads"),
    "POST /api/client/leads/{lead_id}/status": (GAP, "P1: update_lead_status card (needs lead ids in account_state.leads)"),
    "POST /api/client/leads/{lead_id}/note": (GAP, "P2: add_lead_note card"),
    "GET /api/client/lead-capture": (NOT_CHAT, "the form-snippet setup screen for their developer; the chat explains it "
                                               "from account_state.tracking"),
    "GET /api/client/crm": (READ, "leads"),
    "POST /api/client/crm/connect": (NOT_CHAT, _KEY),
    "POST /api/client/crm/disconnect": (GAP, "P3: disconnect_crm card"),
    # ── Online store, READ-ONLY (2026-11-02, docs/STORE.md) ──────────────────
    "GET /api/client/store": (READ, "store"),
    "GET /api/oauth/woocommerce/start": (CHAT, "connect_store"),
    "POST /api/client/store/disconnect": (GAP, "P3: disconnect_store card"),
    # ── Landing pages — full parity (2026-11-02) ─────────────────────────────
    "GET /api/client/landing-pages": (READ, "landing_pages"),
    "POST /api/client/landing-pages": (CHAT, "create_landing_page"),
    "POST /api/client/landing-pages/{page_id}": (CHAT, "edit_landing_page"),
    "POST /api/client/landing-pages/{page_id}/publish": (CHAT, "publish_landing_page"),
    "GET /api/client/landing-pages/{page_id}/versions": (READ, "landing_pages"),
    "POST /api/client/landing-pages/{page_id}/revert": (CHAT, "revert_landing_page"),
    "POST /api/client/landing-pages/{page_id}/unpublish": (CHAT, "unpublish_landing_page"),
    "GET /api/client/landing-pages/{page_id}/preview": (NOT_CHAT, "the preview page itself; the chat's cards link to it"),
    "POST /api/client/landing-pages/{page_id}/regenerate": (CHAT, "edit_landing_page"),
    "DELETE /api/client/landing-pages/{page_id}": (CHAT, "delete_landing_page"),
    "POST /api/client/landing-pages/request-extra": (FIELD, "handoff_topic=package_exception"),
    "POST /api/client/landing-domain": (CHAT, "connect_landing_domain"),
    "POST /api/client/landing-domain/verify": (CHAT, "check_landing_domain"),
    # ── Support, exports, ownership ──────────────────────────────────────────
    "GET /api/client/support": (NOT_CHAT, "the support page's static content; the chat IS the support"),
    "GET /api/client/export/{dataset}": (NOT_CHAT, "a file download; the chat cannot hand over a file"),
    "POST /api/client/export/{dataset}/google-doc": (GAP, "P2: export_to_google_doc card (returns the Doc link in chat)"),
    "GET /api/client/ownership": (GAP, "P3: an ownership section in account_state (what they own, where)"),
    "GET /api/client/ownership/handover": (NOT_CHAT, "a document download; the chat cannot hand over a file"),
    "GET /api/client/export-all": (NOT_CHAT, "a file download; the chat cannot hand over a file"),
    # ── Outreach ─────────────────────────────────────────────────────────────
    "GET /api/client/outreach": (READ, "outreach"),
    "POST /api/client/outreach/mark": (GAP, "P2: mark_outreach_reply card (replied / asked to be removed)"),
    "POST /api/client/outreach/delete": (GAP, "P3: delete_outreach_record card"),
    "POST /api/client/outreach/export": (NOT_CHAT, "a file download; the chat cannot hand over a file"),
    # ── Suggestions ──────────────────────────────────────────────────────────
    "GET /api/client/suggestions": (READ, "approvals"),
    "POST /api/client/suggestions/{suggestion_id}/decide": (CHAT, "approve_suggestion"),
}

# Model output fields a FIELD entry may name
FIELDS = ("upgrade_request", "handoff_topic=package_exception")


def gaps() -> list:
    """[(route, priority, what)] sorted P1 first — the build list."""
    out = []
    for route, (kind, value) in PARITY.items():
        if kind == GAP:
            priority, _, what = value.partition(":")
            out.append((route, priority.strip(), what.strip()))
    return sorted(out, key=lambda g: (g[1], g[0]))
