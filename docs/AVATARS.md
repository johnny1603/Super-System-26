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

### Asking for new poses of a team member

"בקשת תנוחות חדשות" on an agent's block: one line per pose in the client's own words (translated to an
English phrase by one small model call, `business_avatars.to_english`), up to
`agent_characters.MAX_REQUEST_POSES` (4) per request, made together on the REAL pose path (`make_pose`:
the reference set, the cut-out, the library) inside the request
(`POST /api/client/avatars/team/{agent}/poses`, `agent_characters.client_request_poses`). They arrive
pending. These run on uallak's own key (`HIGGSFIELD_AGENTS_KEY`) — our credits, with no per-client limit
beyond 4 per request (an open decision, see the report of 2026-11-25).

## B. "הדמויות של העסק" — the business's own characters (`core/business_avatars`)

Empty at first. The client creates a character from scratch: a name, a described personality, a described
look — a typical-customer persona.

- Technically: ONE generated face (`FACE_PROMPT`, a stylized 3D character — the team's visual family, never
  a photo-real person), stored by us in our Shared Drive (`uallak-agents/business/<client_id>/`). Every pose
  made afterwards sends THAT face as the reference image in the model's documented reference field
  (`media_gen_service.generate_with_references`) → local background removal → verified transparent PNG.
  No vendor-side reference element exists or is used.
- Runs on the CLIENT's own creative-tool key and credits (`media_agent._generation_key`); no key →
  `no_tool` and a button to that tool's card. Nothing here uses uallak's key.
- Record: app_settings `business_avatars:<client_id>` (cleared with the client; the deletion guard covers
  it, as it does `pose_approvals:<client_id>`). Up to 6 avatars, 60 poses each, 4 poses per request.
- A pose is pending until the client approves it; `approved_poses(client_id, avatar_id)` is the seam for
  production.
- **Not wired into posts yet**: a post's `characters` are the uallak agents only (`core/post_brief`). A
  business avatar can be created, posed and approved; no post casts it. The feature catalog says so.

Routes: `POST /api/client/avatars/business` (create), `…/{avatar_id}/poses`, `…/poses/decide`,
`…/{avatar_id}/delete`, `GET …/{avatar_id}/face`, `GET …/pose/{pose_id}/thumb`. Every write is the client's
own session (`_avatars_write`). `tests/test_business_avatars.py`.

Nothing on this page has run against the live vendor.
