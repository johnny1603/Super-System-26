---
name: media
description: How uallak's media agent works — image/video generation via the client-paid Higgsfield Cloud API (per-client keys), per-client Google Drive organization, the sacred Saturday-night weekly check-in, camera coaching kits, and the pull points other agents use. Use when touching agents/media_agent.py, core/media_gen_service.py, drive media folders, or any /api/media* endpoint.
---

# Media agent (creative production hub)

## Role boundaries

Creates VISUAL media only — never text (seo_agent and others write). Other
agents PULL from it; it pushes nothing live. **Human approval is absolute**:
everything lands in the client's Drive folder for review; nothing is ever
auto-published (same principle as PAUSED campaigns / draft posts).

## Vendor + billing decision (revised 2026-07-19)

**Higgsfield Cloud API as the aggregator platform** — fronts Google's models
(Veo-class video, image models incl. its own Soul) plus Kling/Seedance-class
alternatives AND the avatar/voice capabilities the future Avatar Agent will
want. One integration, many models. Endpoint base + model slugs are
env-overridable (`MEDIA_API_BASE`, `MEDIA_IMAGE_MODEL`, `MEDIA_VIDEO_MODEL`)
— the platform is young; a rename is an env fix, not a deploy.

**THE CLIENT PAYS for generation** (business decision — same principle as ad
spend / SEO tools / WP Application Passwords):
- Each client signs up at higgsfield.ai on THEIR OWN payment method and a
  plan sized to their volume (Starter $15 / Plus $39 / Ultra $99 per month,
  Stripe-billed; we never see the card).
- They create an API key at **cloud.higgsfield.ai/api-keys** and paste it in
  themselves via the dashboard's "יצירת מדיה" connection card → stored per
  client (`client_accounts`, platform='higgsfield') via the session-gated
  `POST /api/media/connect` — client self-service, same pattern as the
  WordPress Application Password card (no OAuth exists for Higgsfield
  either). Every generation runs on their key and consumes THEIR plan
  credits — verified: API auth is a per-account Bearer key, and plan credits
  are what API generations draw on, so per-client keys ARE the supported
  multi-tenant path.
- The **Team plan is deliberately NOT used** — it's one shared org wallet
  (us absorbing costs), the opposite of this model.
- **Generation never writes to client_costs** (that table is OUR internal
  costs; client-paid credits there would corrupt margin numbers). Credits
  used are logged on the activity rows instead.
- Daily caps (100 img / 15 video, api_call_counters) protect the CLIENT'S
  credit balance from a runaway loop on our side.

**NOT verified live**: written against the public docs/SDK, never run with a
real key. The job-status/result response shapes are the likeliest one-round
fix on first real use. ToS note: operating a client's account on their key
is the same accepted gray zone as WP Application Passwords.

## Music licensing (business decision — flagged, not built)

Researched: **Artlist Commercial ~$299/yr** (universal license covering
client work + music + SFX + stock footage, simplest licensing) vs **Epidemic
Sound Commercial ~$300/yr** (deepest music/SFX library; client work covered
on the commercial tier). Recommendation: **Artlist** for one-license
simplicity if/when human-edited videos need real tracks — but Veo-class
models generate native audio, so v1's generated clips don't need it yet.
Never use unlicensed copyrighted music; no workaround exists in the code.

## Drive organization (nothing ever gets lost)

Root: `DRIVE_MEDIA_FOLDER_ID` (a folder in Johnny's Drive shared with the
service account as Editor — same setup pattern as the archive folder).
Per client: `client-{id} — {name}/` with `images/{instagram,facebook,tiktok,
website}`, `videos/{instagram,facebook,tiktok}`, `scripts/`, and
`website-media/{page}` (synced from the live site's pages via
`sync_website_media_folders` / `GET /api/media/sync-site-folders`).
The client folder is shared read-only with the client's email on first touch;
they browse it in Drive itself — `GET /api/client/media-folder`
(session-gated) returns the link, surfaced as a button on the profile page.

## The sacred weekly cadence (non-negotiable schedule)

Saturdays 20:00 Israel time (מוצאי שבת) — Cloud Scheduler:

```
gcloud scheduler jobs create http media-weekly-checkin --schedule="0 20 * * 6" \
  --time-zone="Asia/Jerusalem" --uri="{SERVICE_URL}/api/media/weekly-checkin" \
  --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

Per media client (client_agents rows, agent_name='media_agent'): builds
context by REUSING `engagement_agent._client_context` (calendar + trends +
performance — one trend mechanism, never a second one), proposes 2-3 visual
items into **client_suggestions kind='media_plan'** (the existing approval
pipeline; dashboard label added), plus a chat nudge. 5-day dedup makes reruns
harmless. Approval alerts (engagement's decide_suggestion) tell the team to
produce — generation is then triggered via the admin endpoints.

## Camera coaching (delivers the sales-chat promise)

`create_filming_kit(client_id, topic)` → script (client's language,
60-90s spoken, hook-first) + shot list + confidence coaching + gear note,
uploaded to `scripts/` in Drive and announced in the dashboard chat.

## Pull points for other agents

- **meta_content_agent**: `prepare_for_publishing(client_id, file_id)` /
  `POST /api/media/prepare-publish` flips ONE file public and returns a
  direct URL for the publish spec's media_url (client folders stay private).
- **website/seo**: pass that same public URL to
  `wp.upload_media_from_url` (WebP conversion applies as usual).

## Iron rules (generation quality)

- **NO text inside generated images** — every model renders Hebrew badly;
  text overlay is a human/design step. The prompt-crafting system prompt
  enforces this; don't remove it.
- Brand palette (website_agent's `website_brand_identity` activity record)
  feeds the prompt when present.
- Briefs go through a Claude prompt-crafting step (cost_category
  claude_media) — never pass a raw client brief straight to Imagen/Veo.

## Tier 2 extension points (future — do NOT build casually)

- **Avatar agent**: `generate_image/generate_video` accept an
  `avatar_context` dict that v1 ignores (passed into the prompt-crafting
  payload). The future avatar agent enriches through it; needs its own
  handoff + paid add-on pricing.
- **AI podcast**: future format on generate_video + a voice bank. Noted
  direction only.

## Setup

System (one-time): create the media root folder in Johnny's Drive, share
with the service account email (Editor), set `DRIVE_MEDIA_FOLDER_ID`;
create the Saturday scheduler job (above).

Per client — CLIENT SELF-SERVICE via a dashboard connection card (like
WordPress, unlike the admin-only SEO tool connect):
1. Client signs up at higgsfield.ai with their own payment method, picks a
   plan sized to their content volume (guide shown on the card: Starter $15
   covers a light image-only cadence; Plus $39 for weekly video).
2. Client creates an API key at cloud.higgsfield.ai/api-keys.
3. Client pastes it into the "יצירת מדיה" card in their dashboard, which
   calls the session-gated `POST /api/media/connect` — no admin step needed.
4. Admin still assigns the agent: `POST /api/clients/{id}/agents` with
   `media_agent` (that's what makes the client eligible for the Saturday
   check-in and lets the team generate for them).

## Endpoints

Client (session): `POST /api/media/connect` (paste their Higgsfield API
key — the dashboard card), `GET /api/client/media-folder`.
Admin (X-Admin-Key): `POST /api/media/generate-image`,
`POST /api/media/generate-video` (slow — job polling up to ~10 min),
`POST /api/media/filming-kit`, `POST /api/media/prepare-publish`,
`GET /api/media/weekly-checkin`, `GET /api/media/sync-site-folders`.
All plain `def` (blocking HTTP + LLM).
