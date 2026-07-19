---
name: avatar
description: How uallak's avatar agent works — real-person digital twins (HeyGen) and voice clones (ElevenLabs) as a DISTINCT PAID ADD-ON, with mandatory recorded consent, minutes-based tier tracking, and client-owned accounts. Use when touching agents/avatar_agent.py, core/heygen_service.py, core/elevenlabs_service.py, PRICING["avatar"], or any /api/avatar* endpoint.
---

# Avatar agent (premium add-on — real people's likeness)

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
The dashboard card collects it via explicit checkboxes (likeness always;
voice when an ElevenLabs key is given) BEFORE keys are stored; every server
path re-checks with `has_consent()`. HeyGen additionally requires its own
recorded consent-statement VIDEO for twins — the source kit gives the client
the exact sentence. Bump `CONSENT_VERSION` when wording changes. Revocation
is "client asks, we stop + delete" — handled manually for now (flag if it
needs automation).

## HeyGen API reality (re-verified 2026-07-19 — drives the flow's shape)

- **Billing (Feb 2026 migration, confirmed)**: HeyGen's API is
  PAY-AS-YOU-GO — the client tops up an API wallet from $5, no monthly
  commitment, credits expire after 12 months, no free API credits. The API
  wallet is SEPARATE from HeyGen's web plans. Generation runs ~$1/min
  (Avatar III 1080p) to ~$3/min (Avatar V), up to $5/min (Avatar IV 4K) —
  useful margin math: a Basic-tier client (10 min/mo, 450₪) pays HeyGen
  roughly $10-30/mo in credits on top.
- **VIDEO digital twin CREATION API remains ENTERPRISE-ONLY even after the
  migration** (HeyGen help center, current: "only available for Enterprise
  API users" — Enterprise = Pay-As-You-Go + the Digital Twin Creation API).
  The billing change affected generation, not creation access.
- **The decided creation flow (2026-07-19) — workspace invite**: the CLIENT
  invites Johnny's own HeyGen login as a **Creator-role collaborator** to
  their workspace (scoped role: can create avatars/videos/voices, no full
  account access, no password sharing — available on **Team plan and
  above**, NOT confirmed on the cheap Creator solo plan). JOHNNY performs
  the one-time creation step in their workspace — not the client. After the
  avatar exists, ALL ongoing generation runs on the client's own API key
  exactly like Higgsfield — zero further manual involvement for that
  avatar. `create_avatar` still tries the Enterprise API first and returns
  method='workspace_invite' with instructions when it isn't available; the
  **daily readiness scan** (`GET /api/avatar/scan`) detects each finished
  avatar and notifies the client — nobody is left wondering.
- **Client cost implication (disclose everywhere, no surprises)**: because
  of the workspace-invite requirement, avatar-tier clients need HeyGen's
  **Team plan (~$149/mo)**, not the ~$24-29 Creator plan — plus API
  generation credits. Their direct cost, never ours. The dashboard card
  says this explicitly; `PRICING["avatar"]["client_direct_costs_note_he"]`
  carries the canonical Hebrew disclosure for the future proposals
  integration.
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

1. Client connects own HeyGen (+optional ElevenLabs) keys + consents —
   dashboard card → `POST /api/avatar/consent` then `POST /api/avatar/connect`.
2. `POST /api/avatar/request-source` — filming-kit-pattern instructions doc
   into Drive `avatar-source/` (shared to the client as WRITER so they can
   upload); the kit names the consent-clip convention (filename contains
   'consent').
3. `POST /api/avatar/create` — photos → photo-avatar path; videos → twin
   path (Enterprise API, or the workspace-invite flow above). `POST
   /api/avatar/create-voice` for the ElevenLabs clone (instant).
4. Daily scan detects each avatar's readiness (multi-avatar aware, with a
   stop condition so new HeyGen STOCK avatars can't false-trigger) →
   client notified 🎉.
5. `POST /api/avatar/set-tier` (admin; activity-row storage, newest wins) →
   `POST /api/avatar/generate-video` — gates in order: consent → tier →
   minutes remaining (hard block at cap + upsell alert) → accounts. Cloned
   voice preferred (11L TTS → Drive → public URL → HeyGen audio), else a
   HeyGen stock voice id. Finished videos land in Drive `videos/avatar/`
   for human review — never auto-published. `GET /api/avatar/usage` shows
   minutes used/remaining + avatars_ready count (setup-fee basis).
6. **Multi-avatar picker**: clients can hold several avatars (first 150₪,
   each additional 100₪). `GET /api/avatar/list` returns every ready
   avatar; generate-video auto-picks only when exactly ONE exists —
   with several, an explicit `avatar_id` is required (never guess whose
   face fronts a video). One avatar per video; no compositing.

Disconnect: `_DISCONNECT_GROUPS["avatar"]` = heygen + elevenlabs together
(dashboard two-click, offboarding purge covers it automatically).

## Deferred / flagged

- Sales chat, support chat, and unified pricing display for this tier —
  **separate follow-up handoff** (the PRICING exclusion comes off then).
- PayPal billing wiring for the tier fee itself (part of that follow-up).
- Consent revocation automation (v1: client asks → we stop + delete,
  manually).
- VERIFICATION: both services are docs-derived, never run with live keys —
  HeyGen photo-avatar endpoints and the twin-creation payload are the
  likeliest one-round fixes. ElevenLabs voices/add is years-stable.
