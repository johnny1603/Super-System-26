---
name: content-docs
description: How uallak delivers long-form content (full scripts, detailed homework/instructions) that's too dense for the chat — as a real Google Doc in the client's Drive, with a genuine recorded acknowledgment. Use when touching agents/content_docs_agent.py, core/drive_service.upload_google_doc, the content-docs/ Drive subfolder, or any /api/content-docs* endpoint.
---

# Content docs (long-form delivery — real Google Docs, real acknowledgment)

## Why this exists

Some content genuinely doesn't fit in a chat bubble — a full script, detailed
homework instructions — but the chat is still the right place to TELL the
client it exists and get them there fast. `agents/content_docs_agent.py` is
the shared mechanism for that: create the doc, hand the client a link, and
record a REAL confirmation that they saw it — never assume delivery just
because a message was sent.

## Deliberately reuses TWO existing systems, builds neither twice

1. **Storage**: `content-docs/` is a peer of media_agent's `images/`,
   `videos/`, `scripts/` folders under the SAME per-client Drive root
   (`agents.media_agent._subfolder(client_id, "content-docs")`) — not a
   second root folder, not a new Drive integration.
2. **Acknowledgment**: reuses `client_suggestions` (the SAME "ממתין לאישור
   שלך" pending-approval pipeline engagement_agent/media_agent/seo_agent
   already write into), with a fixed `kind="content_doc"`. The client
   acknowledges by tapping "approve" on the card — `decided_at` becomes the
   real, recorded confirmation timestamp. **This is a deliberate adaptation
   of the ask, not a literal copy**: the handoff said "same pattern as the
   avatar consent gate" — but avatar's consent is a checkbox gating an
   action BEFORE it happens, while a content doc already exists by the time
   the client sees it; what's being confirmed here is "I saw it," not "I
   permit this." The invariant that actually matters — a real recorded
   confirmation, never assumed — is preserved by riding `client_suggestions`'
   already-real approve/reject/`decided_at` mechanics, which also satisfies
   the handoff's OTHER explicit instruction to stay "consistent with the
   existing pending-approval pattern... not a separate system." Don't try to
   bolt on a second literal consent-checkbox flow on top of this — it would
   contradict that instruction.

Because it rides `client_suggestions`, the support chat concierge and the
dashboard's pending-approval card already handle it with ZERO code changes
on that side (`support_agent.py`'s `pending_suggestions` context is generic
by title/kind already; the client dashboard's `SUGG_KIND_KEYS` just needed
one new entry for the display label).

## Real Google Docs, not .txt files

`core/drive_service.upload_google_doc(folder_id, filename, html_content)` —
uploads HTML with the metadata's `mimeType` set to
`application/vnd.google-apps.document`; Drive CONVERTS it into a native,
editable Doc (headings/bold/lists carry over), not a downloadable file. This
is a genuine capability gap it filled: `media_agent.create_filming_kit`
today still uploads plain `.txt` via `upload_bytes` — that wasn't touched
here (out of scope for this handoff), but it's a natural future candidate to
switch to `upload_google_doc` for a nicer, editable/commentable result. Any
future long-form deliverable should default to this over a `.txt` upload.

## Sharing role: commenter, not reader or writer

The client gets `commenter` access (`drive_service.share_with_user(...,
role="commenter")`) — they can leave feedback/questions inline on a script
without being able to edit the actual content. Different from media_agent's
root folder (client is `reader`, just browsing) and avatar_agent's
avatar-source folder (client is `writer`, they upload their own footage) —
pick the role that matches what the client should actually be able to do,
don't default to one convention blindly.

## API

- `POST /api/content-docs/deliver` (X-Admin-Key) — `{client_id, title,
  html_content, doc_kind, body}`. `doc_kind` is a free-form label
  ("script"/"homework"/"instructions"/...) for admin/activity context only —
  NOT the `client_suggestions.kind`, which is always the fixed
  `"content_doc"` so the dashboard/chat's generic handling picks it up.
  `body` optionally overrides the approval card's Hebrew ask text.
- `GET /api/admin/clients/{id}/content-docs` (admin session cookie) — the
  drawer's "מסמכי תוכן" section: every doc ever delivered to this client,
  pending vs acknowledged (with the real timestamp).
- `agents.content_docs_agent.get_doc_status(client_id, doc_id)` /
  `has_acknowledged(...)` — for any future caller that needs to check
  delivery status programmatically (e.g. before sending a follow-up nudge).

## Gotchas

- **Never add a `content_doc` handler to `engagement_agent._AUTO_FULFILL`.**
  Approving IS the terminal action here (the acknowledgment itself) — unlike
  `media_plan`, whose approval kicks off real generation. There is nothing
  to auto-execute on approval for this kind.
- The chat notification includes the doc's `webViewLink` as plain text in
  the message — `appendSupportMsg`'s `linkify()` (dashboard/client/index.html,
  added alongside this feature) turns any `http(s)://` URL in ANY chat
  message into a real clickable link, escaping the rest of the text first
  (safe against both client-typed and system-generated content). This
  benefits every existing notification with a link (avatar-ready, filming
  kits, media plans), not just this one — don't revert it to plain
  `textContent` when touching that function again.
- `deliver_doc` is currently ADMIN-triggered only (`POST /api/content-docs/
  deliver`, X-Admin-Key) — there's no agent producing these automatically
  yet. If a future agent (SEO strategy attaching a full brief? avatar
  filming instructions?) wants to deliver a doc, call
  `content_docs_agent.deliver_doc(...)` directly — don't build a second
  Drive-doc-plus-suggestion path.
