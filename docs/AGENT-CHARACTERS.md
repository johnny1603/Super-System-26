# Agents as their own characters (2026-11-24)

The team's agents (maya, lior, yoav, johnny, elinor, nissim, uri) can appear in
a client's post **as themselves**, from their Higgsfield **reference
elements**. No Soul model is trained. Code: `core/agent_characters.py`.
Tests: `tests/test_agent_characters.py`.

## The brief (knowledge/social-craft.md §2.2)

Two per-asset fields in `core/post_brief.AssetBrief`, set by the social agent:

| field | values |
|---|---|
| `characters` | `[]` or agent ids |
| `character_mode` | `composite` or `in_scene`. Not set: `text_slide` → composite, anything else → in_scene |

Refused with a clear line:
- a character on `real_photo`;
- composite on anything other than a text slide, or with more than one character;
- in_scene on a text slide;
- more than 3 characters in one image.

The fields travel through the post item's slides, into production, and onto
the queue items.

## The mapping: agent ↔ element (stored, never hard-coded)

The mapping lives in app_settings `agent_elements`.

**Where it comes from.** The account's element list (`media_gen_service.list_elements`), matched by name:
- **exact:** `uallak-<agent>`, case-insensitive;
- **loose:** the name ends in `uallak-<agent>` as its own token, e.g. `char_uallak-lior`.

**How it resolves.**
- Exact beats loose.
- Two candidates of the best kind are a **conflict**: there is no automatic pick, and the admin chooses.
- Only `completed` elements are usable.
- An override always wins.

**When it is re-read.** Every 6 hours from the minute tick (`/api/tiktok/uploads/run` → `sync_due`), and on the admin button.

**Admin screen.** `/admin/characters/` shows each agent with its element name, id, status and source (auto or override), plus a manual pick. It also lists the unmatched `uallak…` elements, the pose library counts, and the agents' "weak element" reports.

Routes, all behind the admin session:
- `GET /api/admin/characters`
- `POST /api/admin/characters/sync`
- `POST /api/admin/characters/override`

**As read on 2026-11-24 through the Higgsfield MCP:**
- **lior's element** is named `char_uallak-lior`, so it is a loose match.
- **uri has two elements:** `Uallak-uri` (a74cc3b0…) and `uallak-uri` (67e4d7d4…, the idle-animation pose source). That is a conflict, so pick a74cc3b0 on the screen.
- **`uallak-agent-1`** matches no agent.

## IN SCENE

`media_agent._character_scene`:

1. **Reuse first.** The library is checked before anything is made: approved in-scene images of this agent for this client go to the agent's own choice step, at no cost.
2. **Write the scene.** Claude writes the scene around "the character" and never describes its face. Its JSON also returns outfit, setting and framing.
3. **Add the characters in code.** `in_scene_prompt` adds ONE `<<<element_id>>>` per character and, always, "same stylized 3D character look and same face as the reference figure". Any placeholder the model wrote is removed first.
4. **Guard the prompt.** `assert_copy_free` runs on the final prompt.
5. **Generate.** `media_gen_service.generate_with_elements` runs on **uallak's key**. The payload is `{model, prompt, aspect_ratio}`; there is no medias or input-images field, so an element id can never land there.
6. **Store.** The image goes to the client's folder, and a pose record is added for each character (client-scoped, not transparent).

## COMPOSITE

1. **Pose.** `pose_for` looks for an APPROVED transparent cut-out first (`find_poses`). Using one costs nothing and generates nothing.
   - Otherwise `make_pose` runs: the placeholder on a plain background → `remove_background` → `ensure_transparent`.
   - `ensure_transparent` must find real transparent pixels. If not, the remover is reported as failed, never silently accepted.
   - The PNG goes to our Shared Drive under `uallak-agents/<agent>/`, global.
2. **Slide spec.** `knowledge/slide-spec.schema.json` `character`: `{agent, pose_id, x_pct, y_pct, scale_pct, flip, shadow}`.
   - The system sets `agent` and `pose_id` per slide, in the slide entry. The designer sets only the placement, and `slide_design.placement_only` strips anything else.
   - x_pct is the centre, measured from the RIGHT. y_pct is where the feet stand. scale_pct is the height.
3. **Render** (`slide_renderer._character`):
   - **Order:** background → accent → **character** → logo → text, so the text is always on top.
   - **Shadow:** a soft blurred ellipse under the feet, from `true` (0.35), `false`, or 0–1.
4. **Warnings, not crops.** `character_overflow` (past the canvas) and `character_text_overlap` (opaque pixels under a text box) go to the `warnings` list. They become flags on the design card; the render does not fail.
   - A pose without transparency does fail (`character_not_transparent`).
   - A pose that could not be made: the slide renders without it, plus the flag `character_unavailable` and an admin alert.

## The pose library: accumulate, never gate

The library lives in app_settings `agent_poses`.

- A generated image is stored at once, as pending.
- When the manager **approves** a post (`social_queue.approve` → `on_items_approved`, covering item `characters`, a design's spec and slides, and `_finalize`'s items), every character image it used becomes **approved**, tagged with agent, outfit, setting, framing and transparent-or-not.
- `find_poses` offers only approved poses. Ranking is match score (`drive_assets._match_score`) minus the usage penalty (`asset_feedback.penalty`).
- The pose an agent used **last** always ranks last, so the same pose is not repeated in consecutive posts.

## The one guardrail

No agent creates or rebuilds a reference element, its own or another agent's.

- `create_element`, `rebuild_element` and `update_element` always raise `ElementCreationRefused` and alert.
- `media_gen_service` has no element-write call.
- There is no agent action for it.
- An agent that judges an element weak calls `report_element_weak`: an admin alert and a row on the screen, nothing else.
- The prompt rule is in `support_agent.CONTENT_CONTEXT_RULE`.

## Approval-card notices (never blocking)

- `ai_label` goes on every post with a character.
- `ai_person_forbidden` is added when the client's brand brief rules out AI-generated people (`AI_PEOPLE_FORBIDDEN_RE`).

Both are in `social_queue.FLAG_TEXT`, in 5 languages.

## The account and the key

Elements are scoped to the Higgsfield account that created them, so these generations and the background removal run on uallak's own key, `HIGGSFIELD_AGENTS_KEY` (`id:secret`, registered in `keys_agent.KEYS`). Set it once with `bash scripts/set_higgsfield_agents_key.sh`: hidden input → Secret Manager → service.

Without the key, the screen says "not configured" and a character asset fails with `characters_not_configured`, plus an admin alert. uallak pays for these credits; they still count against the post's `budget_credits`.

## NOT run live

Higgsfield's public API docs describe neither elements nor background removal. Everything below is our reading of their MCP tool, kept as env settings, so a mismatch is a Cloud Run env fix:

| setting | default |
|---|---|
| element list path | `HIGGSFIELD_ELEMENTS_LIST_PATH=reference-elements` |
| generation path | `HIGGSFIELD_ELEMENT_IMAGE_PATH=generations` |
| image model | `HIGGSFIELD_ELEMENT_IMAGE_MODEL=nano_banana_pro` |
| video model | `HIGGSFIELD_ELEMENT_VIDEO_MODEL=seedance_2_0` |
| background removal path | `HIGGSFIELD_BG_REMOVE_PATH=remove-background` |

The screen's "re-read now" shows exactly what came back.

## How to ask

- **Composite:** "מאיה, תכיני קרוסלה של 3 טיפים ותשימי את עצמך בשקופית הראשונה" → a text slide with `characters: ["maya"]` (mode by default).
- **In scene:** "ליאור, תמונה שלך עובד בסטודיו של הלקוח, ליד השולחן" → a `generated_image` with `characters: ["lior"]` (in_scene by default); "Maya and Lior together at the workshop" → `["maya","lior"]`.
