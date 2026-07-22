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
- **The decided creation flow (2026-07-20) — client self-creates**: the
  CLIENT creates their own twin in HeyGen's web UI, a few clicks following
  the source kit (which names the exact steps + the consent-statement
  video HeyGen requires). Works on whatever plan they're on, incl. free.
  No workspace invite, no collaborator step, no Johnny involvement.
  Then they connect their HeyGen API key via the existing dashboard card
  (unchanged) and ALL generation runs on that key exactly like Higgsfield.
  `create_avatar` still tries the Enterprise API first and returns
  method='web_ui_self_service' (+ a dashboard-chat message pointing the
  client to the web UI) when it isn't available; the **daily readiness
  scan** (`GET /api/avatar/scan`) detects each finished avatar and
  notifies the client — nobody is left wondering.
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

1. Client connects own HeyGen (+optional ElevenLabs) keys + consents —
   dashboard card → `POST /api/avatar/consent` then `POST /api/avatar/connect`.
2. `POST /api/avatar/request-source` — filming-kit-pattern instructions doc
   into Drive `avatar-source/` (shared to the client as WRITER so they can
   upload); the kit names the consent-clip convention (filename contains
   'consent').
3. `POST /api/avatar/create` — photos → photo-avatar path; videos → twin
   path (Enterprise API, or the client-self-creates web-UI flow above).
   `POST /api/avatar/create-voice` for the ElevenLabs clone (instant).
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
