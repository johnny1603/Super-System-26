# Agents as their own characters (2026-11-24)

The team's agents (maya, lior, yoav, johnny, elinor, nissim, uri) can appear in
a client's post **as themselves**. Each agent has one **reference image** that
the manager sets by hand. No Soul model is trained. Code:
`core/agent_characters.py`. Tests: `tests/test_agent_characters.py`.

## Why reference images, not "elements" (fixed the same day)

The first version (052b1d6) read Higgsfield reference elements from a list
endpoint inferred from their MCP tool descriptions. Production answered
`405 Method Not Allowed`: the public REST API at `https://api.higgsfield.ai`
has no endpoint that lists elements (docs.higgsfield.ai, read 2026-11-24).
Elements are a web-UI and MCP feature. Nothing in the system lists, matches or
re-reads anything now, and the 6-hourly re-read is gone.

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

## The character workspace (`/admin/characters/`) — the manager's only

One card per agent, three parts, top to bottom:

1. **Reference set** — what defines the character. Several images (up to
   14): add by link (+ optional media id) or upload several files at once;
   each shows as a thumbnail with ✕ to remove it; "ניקוי כל הסט" empties it;
   optional notes (outfit, framing). The WHOLE set is sent at generation,
   in order, within the model's max (Grok 10, Qwen 3); several characters in
   one image share the max, at least one image each (`reference_plan`), and
   the prompt names them by their images ("reference images 1-3"). Each tile
   says whether it is sent or beyond the max. More references = better face
   consistency: this is the main quality lever.
2. **Test poses** ("בדיקת תנוחות") — a short pose instruction (or the three
   fixed varied poses by default) and a count (1–3) → "יצירת תנוחות". Runs in
   the background on the SAME path as a real post's composite pose
   (`pose_prompt`: the set, the style clause, the plain background, the
   configured model, 3:4); the page polls; credits per result and per run.
   Each result waits for the manager: **"שמירה למאגר"** (background removed,
   verified transparent, into the library APPROVED and tagged with the pose /
   outfit / framing typed on the card — reused by posts like any library
   pose), **"מחיקה"**, or **"הפיכה לתמונת ייחוס…"** — opens a warning
   (references should come from approved source images; identity built from
   generated output drifts) and only its own button promotes. Nothing is
   saved or promoted automatically.
3. **Pose library** — counts (approved / pending / transparent / total) and
   the newest 60 poses with ✕ to remove one (its file trashed in our Drive;
   a client's in-scene image is only unlisted).

Every write takes `by="admin"`; anything else is refused and alerted.

Stored in app_settings `agent_references` (the sets) and
`agent_pose_candidates` (test results + the running test).

- **Paste:** the URL is fetched and checked to be a real image (PNG, JPEG,
  WEBP or GIF, up to 10 MB) before it is saved.
- **Upload:** the file goes through Higgsfield's documented upload —
  `POST /files/generate-upload-url {content_type}` → PUT the bytes to
  `upload_url` with `upload_headers` (our key never goes there) →
  `public_url`.
- **Why a copy in our Drive:** Higgsfield tags uploads
  `retention=temporary` and keeps generation outputs about 7 days. A stored
  URL older than 24 h is re-uploaded from our copy before it is sent. If the
  copy could not be kept (no Drive), the screen says so and the URL is used as
  is.

Routes, all behind the admin session:
- `GET /api/admin/characters` (the workspace)
- `POST /api/admin/characters/reference` `{agent, url, media_id}` · `/upload` `{agent, content_b64, media_id}`
- `POST /api/admin/characters/reference/remove` `{agent, ref_id}` · `/notes` `{agent, notes}` · `/clear` `{agent}`
- `POST /api/admin/characters/generate` `{agent, instruction, count}`
- `POST /api/admin/characters/candidate/save` `{agent, cand_id, pose, outfit, framing}` · `/candidate/discard` · `/candidate/promote` `{…, confirm: true}`
- `POST /api/admin/characters/pose/remove` `{pose_id}` · `GET /api/admin/characters/pose/{pose_id}` (the thumbnail)

**No reference = a loud failure.** `MissingReference` carries
"לא מוגדרת תמונת ייחוס עבור <agent>" (e.g. "… עבור ליאור (lior)"). In scene,
the asset fails with `character_missing_reference`; on a composite slide, the
slide renders without the character. Either way the message goes into
`production.character_errors` and into the production line in the manager's
chat (`post_pipeline._tell`), and the post is not "done".

## IN SCENE

`media_agent._character_scene`:

1. **Reuse first.** The library is checked before anything is made: approved in-scene images of this agent for this client go to the agent's own choice step, at no cost.
2. **References.** Every character must have its reference (else the explicit error above). A video with a character is refused plainly: none of the documented video pages read take character references.
3. **Write the scene.** Claude writes the scene around "the character" and never describes its face. Its JSON also returns outfit, setting and framing.
4. **Name the characters in code.** `in_scene_prompt` names each one by its reference image ("the character from reference image 1", …, in the order the images are sent) and, always, "same stylized 3D character look and same face as the reference figure". Any `<<<…>>>` placeholder the model wrote is removed.
5. **Guard the prompt.** `assert_copy_free` runs on the final prompt.
6. **Generate.** `media_gen_service.generate_with_references` runs on **uallak's key**, with the stored images in the model's documented field:

| model (path) | field | images |
|---|---|---|
| `xai/grok-imagine-image-2.0` (default) | `image_urls` | 0–10 |
| `alibaba/qwen-image-3/edit` | `image_urls` (required) | 1–3 |

   Both take several images, so several characters go into one image. The
   payload is `{prompt, <the model's own reference field>, aspect_ratio}`
   (`gen.CHARACTER_MODELS`: field, max, aspect enum per path). Switch model
   with `HIGGSFIELD_CHARACTER_IMAGE_PATH`; a path that is NOT in the list is
   refused with a clear line, never sent.
   **A true 4:5.** A model whose enum has the requested ratio gets it and
   nothing is cropped. Otherwise it gets its NEAREST ratio (Grok 3:4, Qwen
   7:9) and `fit_aspect` crops the result from the centre (4:5 from 3:4 =
   6.25% off the height); the prompt then asks for room above the head and
   below the feet. On a cut-out (composite) the crop window moves to keep
   every visible pixel; a figure that does not fit is left uncropped with the
   warning `character_crop_skipped`, never clipped.
   **Nano Banana Pro / FLUX 3** exist on Higgsfield's MCP platform
   (`nano_banana_pro`: `image_references` ≤14, has 4:5; `flux_3_image`: ≤10,
   has 4:5) but are NOT in the REST docs. `bash scripts/higgsfield_probe_paths.sh`
   (Cloud Shell, uallak's key) asks the server: no prompt + an invalid
   aspect_ratio, so a real path answers 422 and nothing is generated; controls
   run first; anything accepted is cancelled. A model is added only on a
   verified 422.
7. **Store.** The image goes to the client's folder, and a pose record is added for each character (client-scoped, not transparent).

## COMPOSITE

1. **Pose.** `pose_for` looks for an APPROVED transparent cut-out first (`find_poses`). Using one costs nothing and generates nothing.
   - Otherwise `make_pose` runs: the reference set + the pose on a plain background → background removal IN OUR CONTAINER (`core/background_removal`, u2netp on onnxruntime, free) → `ensure_transparent`.
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
   - A pose that could not be made: the slide renders without it, plus the flag `character_unavailable` and an admin alert (and the explicit message when the reference is missing).

## The pose library: accumulate, never gate

The library lives in app_settings `agent_poses`.

- A generated image is stored at once, as pending.
- When the manager **approves** a post (`social_queue.approve` → `on_items_approved`, covering item `characters`, a design's spec and slides, and `_finalize`'s items), every character image it used becomes **approved**, tagged with agent, outfit, setting, framing and transparent-or-not.
- `find_poses` offers only approved poses. Ranking is match score (`drive_assets._match_score`) minus the usage penalty (`asset_feedback.penalty`).
- The pose an agent used **last** always ranks last, so the same pose is not repeated in consecutive posts.

## The one guardrail

No agent creates or rebuilds a character, its own or another agent's.

- `set_reference` and `clear_reference` accept only `by="admin"`; anything else is refused and alerted.
- `create_element`, `rebuild_element` and `update_element` always raise `ElementCreationRefused` and alert.
- `media_gen_service` has no character-write call, and there is no agent action for it.
- An agent that judges a character weak calls `report_element_weak`: an admin alert and a row on the screen, nothing else.
- The prompt rule is in `support_agent.CONTENT_CONTEXT_RULE`.

## Approval-card notices (never blocking)

- `ai_label` goes on every post with a character.
- `ai_person_forbidden` is added when the client's brand brief rules out AI-generated people (`AI_PEOPLE_FORBIDDEN_RE`).

Both are in `social_queue.FLAG_TEXT`, in 5 languages.

## The account and the key

These generations, the uploads and the background removal run on uallak's own key, `HIGGSFIELD_AGENTS_KEY` (`id:secret`, registered in `keys_agent.KEYS`). Set it once with `bash scripts/set_higgsfield_agents_key.sh`: hidden input → a check with the documented upload-URL call → Secret Manager → service.

Without the key, the screen says "not configured", an upload is refused, and a character asset fails with `characters_not_configured`, plus an admin alert. uallak pays for these credits; they still count against the post's `budget_credits`.

## What ran live (2026-11-25)

- uallak's key authenticates and `xai/grok-imagine-image-2.0` exists (the probe's control answered 422).
- A character generation, an upload and a background removal have NOT run yet.
- **Background removal is local** (2026-11-25): Higgsfield's REST API has none — the verify script's three candidate paths all answered 404 model_not_found. `core/background_removal` runs rembg's u2netp model (SHA-256 pinned, baked into the image by the Dockerfile) on onnxruntime: no credits, no vendor. Measured: about +50 MB resident after the first removal, a peak of about +170-200 MB for under a second per removal; at 512Mi that is not comfortable — see the deploy note. If the model file is missing, a new pose / "שמירה למאגר" fails with a clear message; approved stored poses keep working.

## How to ask

- **Composite:** "מאיה, תכיני קרוסלה של 3 טיפים ותשימי את עצמך בשקופית הראשונה" → a text slide with `characters: ["maya"]` (mode by default).
- **In scene:** "ליאור, תמונה שלך עובד בסטודיו של הלקוח, ליד השולחן" → a `generated_image` with `characters: ["lior"]` (in_scene by default); "Maya and Lior together at the workshop" → `["maya","lior"]`.
