---
name: avatar
description: How uallak's avatar agent works — the client's presenter character (Higgsfield, short lip-synced clips; HeyGen RETIRED 2026-10-31) and voice clones (ElevenLabs) as a DISTINCT PAID ADD-ON, with mandatory recorded consent, minutes-based tier tracking, and client-owned accounts. Use when touching agents/avatar_agent.py, core/avatar_video.py, core/elevenlabs_service.py, PRICING["avatar"], or any /api/avatar* endpoint.
---

# Avatar agent (premium add-on — real people's likeness)

## CURRENT STATE (2026-10-31) — read this first; HeyGen sections below are HISTORY

- **HeyGen is retired** (its avatar-creation API is Enterprise-only and it sent clients out of
  the product). `core/heygen_service.py` and `avatar_video.HeyGenProvider` stay but are
  UNREACHABLE; nothing client-facing may name or reach them (`tests/test_presenter_higgsfield.py`).
  Stored HeyGen rows are kept. The daily `avatar-scan` job is a no-op and can be deleted.
- **The presenter = Higgsfield**, `core/avatar_video.make_video`: a character image (the
  client's own photo with likeness consent, or an AI image media_agent made, no consent) +
  audio (an uploaded recording or the ElevenLabs clone) → model
  `media_gen_service.PRESENTER_MODEL` (`wan2_7`, `start_image` + `audio_references`) → Drive
  `videos/avatar/`. Clips are **≤ 15 s**; longer = refused with a split offer, never cut, no
  stitching. Public links only during the render.
- **Gate:** internal (billing_exempt) clients only until a live run; `PRESENTER_VIDEO_ALL_CLIENTS=1`
  opens it. `CONSENT_VERSION` `2026-10-31.v4`. Full detail: `docs/CREATIVE-TOOLS.md`.
- Tier "~videos" estimates below assume ~30 s per video; with 15 s clips they are a business
  decision still to revisit.

## Why separate from media_agent (never merge)

Higgsfield generates INVENTED consistent characters; **HeyGen** is the
specialist for true digital twins of a real person's face (accurate
lip-sync, reusable custom avatars) and **ElevenLabs** for professional voice
cloning. Different tools, different job, different billing bucket. Regular
social content = media_agent/Higgsfield under standard management fees;
avatar/cinematic content = THIS agent under its own paid tier. Never blur
them in pricing, proposals, or usage tracking.

## Pricing (FINAL — PRICING["avatar"] in onboarding_agent, exact numbers)

- Setup: **first avatar 150₪**, **each additional 100₪** (one-time).
- Monthly, billed/tracked by **MINUTES** (video counts are client-facing
  estimates at ~30s average, for conversation only):

| Tier | Minutes/mo | ~Videos | Price |
|---|---|---|---|
| basic | up to 10 | ~20 | 450₪/mo |
| advanced | up to 20 | ~40 | 800₪/mo |
| enhanced | up to 40 | ~80 | 1,550₪/mo |
| custom | 40+ | 80+ | custom quote |

Always separate from — and in addition to — the client's own direct
HeyGen/ElevenLabs subscriptions (their card, never ours). **The avatar block
is EXCLUDED from build_proposal's prompt payload** (see the exclusion in
onboarding_agent) — sales/support/pricing-display integration is a separate
follow-up handoff; remove the exclusion only there.

## Consent — mandatory, recorded, not reinterpretable

No creation path (avatar, voice, video generation) runs without a logged
`avatar_consent_recorded` activity row for the relevant scope
(`likeness` / `voice`): statement text + `CONSENT_VERSION` + timestamp.
Each consent sits on its OWN tool's card (core/vendor_tools, 2026-10-30):
likeness on the HeyGen card (required to connect HeyGen), voice-cloning on
the ElevenLabs card (optional — without it a recording is only ever an audio
track, never cloned). Every server path re-checks with `has_consent()`. HeyGen additionally requires its own
recorded consent-statement VIDEO for twins — the source kit gives the client
the exact sentence. Bump `CONSENT_VERSION` when wording changes (v2,
2026-10-30: the statements say "the account I opened for this" instead of the
vendor's name — vendor names are never client-facing, see
docs/CREATIVE-TOOLS.md §6; rows recorded under v1 stay valid; v3,
2026-10-31: the likeness statement says the files are opened to the tool
only for the creation and closed right after — what core/public_share does). Revocation
is "client asks, we stop + delete" — handled manually for now (flag if it
needs automation).

## HeyGen API reality (re-verified 2026-07-20 — drives the flow's shape)

- **Billing (Feb 2026 migration, confirmed)**: HeyGen's API is
  PAY-AS-YOU-GO — the client tops up an API wallet from $5, no monthly
  commitment, credits expire after 12 months, no free API credits. The API
  wallet is SEPARATE from HeyGen's web plans — **standalone, works for
  free-plan users too** ("any user—including free users—can unlock…
  features by purchasing any amount of API credits", help center).
  Generation runs ~$1/min (Avatar III twin 1080p) to ~$3/min (Avatar IV
  photo) and ~$4/min (Avatar IV twin) — useful margin math: a Basic-tier
  client (10 min/mo, 450₪) pays HeyGen roughly $10-40/mo in credits on top.
- **VIDEO digital twin CREATION API remains ENTERPRISE-ONLY** — but that
  gates only the API. **Web-UI twin creation is included on EVERY plan,
  including Free** (pricing page: Free = 1 Custom Digital Twin; Creator
  $29 / Pro $49 = "1+"; Business $149 = 5). The earlier belief that
  creation itself needed a Team/Business plan was wrong.
- **Creation flow (2026-10-31) — the client never leaves uallak by
  default**: they upload the training video and record the consent clip in
  the dashboard's "הסרטונים לדמות שלך" card (core/avatar_footage — two
  labelled slots; the slot is an appProperty on the file, never a filename
  rule; uploads ride /api/client/chat-upload with `X-Avatar-Slot`), then
  press "create" (`POST /api/client/avatar-footage/create`) and
  `create_avatar` submits both via the creation API. ONLY when the tool
  refuses for a permission/plan reason: method='manual_fallback' — ONE plain
  Hebrew chat line (MANUAL_FALLBACK_HE) + a team alert to walk the client
  through the tool's website. The source kit never tells the client to
  create it themselves. The **daily readiness scan** (`GET
  /api/avatar/scan`) detects each finished avatar either way.
- **Re-checked 2026-10-31 (HeyGen developer docs, "avatar consent")**:
  submitting a PRE-RECORDED consent video via API is Enterprise-only; for
  everyone else HeyGen's v3 flow records consent on HeyGen's hosted page
  (`POST /v3/avatars/{group_id}/consent` → a 24h single-use URL, optional
  `reroute_url` back to us). So on a normal key the API path refuses and the
  manual fallback runs. Not built (needs a live key): the v3 group + hosted
  consent link opened from the dashboard with reroute back.
- **Privacy**: the tool takes files only as public URLs. Every share goes
  through `core/public_share.briefly_public` — revoked on success AND
  failure, read back (`drive_service.revoke_public`), retried once, admin
  alert if still public. Same for avatar-video audio tracks and character
  images. The only standing `make_file_public` left is the admin
  Meta-publishing helper (`media_agent.prepare_for_publishing`);
  tests/test_avatar_in_product.py fails on any new one.
  UNVERIFIED: whether the tool fetches the URL during the call or later —
  if later, the immediate revoke breaks creation; first live test decides.
- **Client cost implication (disclose everywhere, no surprises)**: NO paid
  HeyGen subscription is required — one avatar works on the Free plan;
  **additional avatars (our 100₪-each pricing) need a paid plan/add-on**.
  Ongoing generation is paid from the pay-as-you-go API wallet (~$1-4/min).
  Their direct cost, never ours. The dashboard card says this explicitly;
  `PRICING["avatar"]["client_direct_costs_note_he"]` carries the canonical
  Hebrew disclosure for the future proposals integration.
- **Photo Avatar** create/train: available via the standard API ✓ (fully
  automated here).
- Video generation with any existing avatar: standard pay-as-you-go API ✓.
  Duration from `video_status` is what minutes-tracking bills against.

Scheduler (daily):

```
gcloud scheduler jobs create http avatar-scan --schedule="0 9 * * *" \
  --time-zone="Asia/Jerusalem" --uri="{SERVICE_URL}/api/avatar/scan" \
  --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

## Flow

1. Client connects each tool on its own card — `POST /api/client/tools/{tool}/connect`
   (key checked with the vendor first; a rejected key is never stored), plus
   `/test`, `/disconnect`, and `/api/client/tools/provider` for the video
   provider (HeyGen | Higgsfield, core/avatar_video). Voice recordings are
   uploaded in the "הקלטות קול" card (`/api/client/voice*`, core/voice_recordings).
   `POST /api/avatar/connect` is LEGACY (each key given is connected on its own).
2. `POST /api/avatar/request-source` — filming-kit-pattern instructions doc
   into Drive `avatar-source/`; it points to the dashboard card for both
   videos and carries the SAME consent sentence the card shows
   (`avatar_footage.consent_sentence`). Old files in avatar-source still
   work (filename 'consent' = the fallback only for un-slotted files).
3. `POST /api/client/avatar-footage/create` (client) or `POST
   /api/avatar/create` (admin) — photos → photo-avatar path; videos → twin
   path (creation API, or the manual fallback above).
   `POST /api/avatar/create-voice` for the ElevenLabs clone (instant).
4. Daily scan detects each avatar's readiness (multi-avatar aware, with a
   stop condition so new HeyGen STOCK avatars can't false-trigger) →
   client notified 🎉.
5. `POST /api/avatar/set-tier` (admin; activity-row storage, newest wins) →
   `POST /api/avatar/generate-video` — gates in order: consent → tier →
   minutes remaining (hard block at cap + upsell alert) → then
   `core/avatar_video.make_video` (provider + voice: `recording` = an
   uploaded file as the audio track, no cloning consent needed; `clone` =
   11L TTS → Drive → public URL; none = HeyGen stock voice id) names any
   missing tool with a button to only that tool's card. Finished videos land in Drive `videos/avatar/`
   for human review — never auto-published. `GET /api/avatar/usage` shows
   minutes used/remaining + avatars_ready count (setup-fee basis).
6. **Multi-avatar picker**: clients can hold several avatars (first 150₪,
   each additional 100₪). `GET /api/avatar/list` returns every ready
   avatar; generate-video auto-picks only when exactly ONE exists —
   with several, an explicit `avatar_id` is required (never guess whose
   face fronts a video). One avatar per video; no compositing.

Disconnect: per tool (`/api/client/tools/{tool}/disconnect`, or the
`heygen` / `elevenlabs` disconnect groups) — never both together. The old
`"avatar"` group is kept only for an old dashboard tab. Offboarding purge
covers every row automatically. See docs/CREATIVE-TOOLS.md.

## Sales chat + support chat + pricing reference (wired 2026-07-20)

`onboarding_agent.build_proposal` now sees `PRICING["avatar"]` (the exclusion
is gone — BUDGET PYRAMID #9 there is the relevance rule: only offered when
the business is genuinely camera-forward-content-shaped, e.g. personal-brand
services or a client already comfortable on camera per `camera_comfort` —
NOT a default line in every proposal). Setup fee stacks on the setup floor;
the recommended monthly tier (default: basic) is its own `monthly_breakdown`
line INCLUDED in `monthly_management_total` (same treatment as the new-site
hosting line) — the client's own HeyGen/ElevenLabs cost stays a separate
`honest_note` disclosure, same pattern as ad spend/SEO tools. An EXPLICIT
client request (sales chat or `support_agent`'s in-chat upgrade path, which
reuses the exact same `build_proposal` call) always overrides the relevance
filter — the filter only gates PROACTIVE suggestions.

`admin_service.get_pricing_reference()` (`GET /api/admin/pricing`, admin
dashboard's "מחירון מלא" tab) now surfaces the avatar tier alongside every
other pricing number, pulled live from `PRICING` — never a second copy.

## Self-service purchase (upgrade panel, 2026-07-23)

Avatar is directly billable from the dashboard's upgrade panel — the ONLY
add-on that is, because its pricing is exact in PRICING (150₪ setup,
450/800/1550₪ monthly tiers); website/SEO/automation stay chat-routed
(scope-dependent). `onboarding_agent.get_avatar_upgrade_tiers(current_fee)`
computes ADDITIVE tier entries per client at request time; the standard
PayPal plan-revision flow bills the new recurring total. On PayPal-confirmed
success, `/api/upgrade-success`: appends (never replaces) the avatar tier to
the package name, AUTO-ASSIGNS the tier (`avatar_agent.set_tier` —
deterministic bookkeeping, so generation gates work immediately),
AUTO-INVOICES the one-time setup fee via the same `create_invoice` mechanism
checkout uses for the original setup fee (a plan REVISION cannot charge a
one-time fee; the invoice closes the manual-collection gap — the honest
remaining friction is inherent to invoicing: it's a payment REQUEST the
client pays from their email, not an instant charge, identical to checkout's
own setup fee; invoice failure is non-fatal and the alert says to collect
manually), and alerts the team with the one remaining manual step: avatar
onboarding (HeyGen key + consent + source kit). Double-buy guard: avatar tiers are offered/accepted only while no
avatar tier is assigned (`_client_has_avatar_tier`, FAILS CLOSED on read
errors). Consent is NOT bypassed by purchase — creation/generation still
hard-gate on the recorded consent row exactly as before.

## Deferred / flagged

- PayPal billing wiring for the tier fee itself (the tier's monthly fee is
  now included in `monthly_management_total`, so it rides the client's
  existing subscription amount automatically — no separate billing plumbing
  needed; flag if a future change wants it billed as its own item).
- The one-time avatar setup fee is auto-INVOICED (see above) but not
  auto-CHARGED — a truly instant one-time charge alongside a plan revision
  would need a separate PayPal Orders flow with a second client approval
  redirect; not built, and probably not worth it (checkout's own setup fee
  lives with the same invoice-based friction). Whether it gets PAID is now
  tracked — see `INVOICING.INVOICE.PAID`/`.CANCELLED` webhook handling and
  the invoice-aging scan in `core/api_server.py` (2026-07-23), which covers
  this invoice AND the original checkout setup-fee invoice the same way.
- Consent revocation automation (v1: client asks → we stop + delete,
  manually).
- VERIFICATION: both services are docs-derived, never run with live keys —
  HeyGen photo-avatar endpoints and the twin-creation payload are the
  likeliest one-round fixes. ElevenLabs voices/add is years-stable.
