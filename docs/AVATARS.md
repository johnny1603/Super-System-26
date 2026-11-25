# Avatars — the characters in a client's posts (2026-11-25)

Client page **"אווטרים"** (main nav, `#avatars`, `dashboard/client/index.html`). The admin page
`/admin/characters/` is unchanged: it is where reference images are set and poses are seeded.

## A. "הצוות של וואלק" — the uallak agents' pose libraries

Every agent (`agent_characters.AGENTS`) with the poses this client can decide on: the shared cut-outs
(`client_id` 0) and that client's own scenes.

### Approval is the client's, per client

- A pose enters the library **pending**. The client approves it — one pose (✓ on the card), every
  waiting pose of an agent (one button), or a selection (checkbox, Shift for a range, "select all",
  or a mouse drag over the cards; Ctrl / Cmd adds — the media library's behaviour).
- Stored per client: app_settings `pose_approvals:<client_id>` =
  `{"approved": {pose_id: at}, "rejected": {pose_id: at}}` (`agent_characters.decide_poses`). One
  client's decision never touches another's. No migration.
- A pose that client's own approved post carried (`approved_item` "<client>:…") counts as approved
  for that client only.
- The admin's Keep (`save_candidate`) sets the library's own `approved` flag: that is **seeding**
  (what the admin workspace lists), not any client's approval.

### What a post does

`slide_design.attach_characters(..., client_id=)` → `agent_characters.poses_for(client_id=)`:

- uses ONLY a cut-out that client approved (their approved pose even when it was the last one
  used — a repeat beats no character);
- none approved → `NeedsApproval`: **nothing is generated in its place and no unapproved pose is
  used**. The slide is drawn without the character and that is loud: an admin alert
  ("client N: post … — agent character not drawn on …"), the flag `character_needs_approval` on
  the design card with a button to this page, the line in the production's problems (the chat
  line and the progress card), and the production state `partial`;
- the slide remembers what it asked for (`wants_character`). When the client approves a pose,
  `slide_design.apply_approved_poses` draws it into every design still waiting (new `rev`, the
  flag leaves the card) — the post does not have to be produced again.

Routes: `GET /api/client/avatars`, `GET /api/client/avatars/pose/{id}/thumb` (grid size, cached in
memory), `POST /api/client/avatars/poses/decide` (the client's own session only — the admin's
read-only view cannot decide). `tests/test_pose_approval.py`.
