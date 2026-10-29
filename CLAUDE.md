# uallak / Super-System-26

AI-powered autonomous marketing agency for Israeli SMBs. A client goes through a sales chat
(`/chat/`), gets a proposal with 1-2 packages, pays via PayPal (Sandbox only for now), and gets
a session-gated client dashboard (`/dashboard`). Marketing execution agents (real Google/Meta/
TikTok campaign management) do not exist yet — today the system sells; it doesn't yet deliver.

## Critical environment facts (read before running anything)

- This dev machine has **no git, no gcloud, no usable Python** on PATH. Nothing can be run or
  tested locally. Commits happen in GitHub Desktop (user does it), gcloud runs in Cloud Shell
  (user does it). **Use the `deploy` skill before shipping anything.**
- Deployed on Cloud Run: service `super-system`, region `me-west1`, project
  `super-system-500410`. Repo: `johnny1603/Super-System-26` (private).
- The container filesystem (`data/`) is **ephemeral** — wiped every deploy/restart.

## Layout

- `core/api_server.py` — FastAPI app, ALL routes. **The root `/` static mount must stay the
  last registration in the file** or it swallows `/api/*` (caused a real outage once).
- `core/claude_json.py` — `safe_claude_json_call()`: the required helper for every LLM call
  that expects JSON (truncation-aware retry, fence stripping, `ClaudeJSONError`). An EMPTY
  completion (Haiku 5.5 thinks by default and can end a turn with thinking blocks only) is
  retried once on the same model, then once on `DEFAULT_MODEL`, logging stop_reason/usage/block
  types only; text is joined from ALL text blocks.
  `claude_web_search_call()` is its TEXT-mode sibling for answers that need live web search
  (server-side web_search tool, pause_turn handling, per-search fee tracked in client_costs) —
  search + citations don't mix with strict JSON output, hence two paths. Used by the support
  chat's two-stage flow (JSON call gates → emits web_search_query → text call searches).
- `core/agent_base.py` — `log_step` / `timed_step` / `agent_alert`: standard logging+alerting.
- `core/client_journey.py` — the client's milestone track, **derived at read time** from
  rows that already exist (activity, accounts, leads, PayPal). There is deliberately no
  milestones table: a parallel tracker would drift from the activity log invisibly. Also
  owns the **package-derived connection checklist** (`required_connections` /
  `connection_status`) — the required integrations come from the purchased package's
  `recommended_services`, never a guess, and **fail closed** when the package can't be
  read. `note_connections_complete()` stamps the 90-day clock start once, permanently.
  See `HANDOFF-engagement-upgrade.md`.
- `core/feature_catalog.py` — **the** list of what uallak offers a client today, one entry
  per capability. Two readers and no second copy: `interview_agent` renders it (filtered to
  the client's package) so the first-login conversation can actually explain the product,
  and feature-announcement targeting decides relevance from the same entries. **Shipping a
  client-facing capability means adding one dict here** — miss it and the interview keeps
  describing a product that no longer matches. The `access` field (`self_serve` / `manual` /
  `automatic`) is what stops a manual service like ManyChat/Make being described as a button.
- `core/feature_announcements.py` + `engagement_agent.run_feature_announcement` — Johnny
  flags a shipped feature as ready; relevant clients are told **once each**, at their next
  login, in the owning persona's voice and in that persona's chat thread. Admin-triggered
  only, never automatic on a deploy. Needs `migrations/2026-08-08-feature-announcements.sql`;
  does nothing at all until it runs. See `HANDOFF-feature-announcements.md`.
- `core/client_knowledge.py` — **the** read of what we know about a client's BUSINESS
  (sales-chat answers minus financials, interview notes + competitors, cached-only
  competitor research up to 90 days old with its date, 90-day plan + cycle day, milestones,
  suggestion decisions). Fed to the concierge and every persona as `client_knowledge`.
  Read-only, no LLM/paid calls, no new storage; failed sources are left out silently. Keeps
  OUT: financial answers, contact details, `ads_goal_set` (internal), the SEO strategy.
  See `HANDOFF-client-knowledge.md`.
- `core/lead_tracking.client_sales_lead` — the ONE "which lead row is this client's"
  lookup. Skips in-chat upgrade quotes (they are `leads` rows too). Never query `leads` by
  client email/id directly.
- `agents/interview_agent.py` — the onboarding conversation that replaced the static tour,
  in two phases: `connection_nudge()` at first login while integrations are missing, then
  the deep interview **gated** on every required connection being live. Explains AND
  gathers (competitors with links above all), routes domain turns through
  `support_agent.PERSONAS`, runs inside the existing chat window. Captured facts land in
  `client_activity` and are read back through `core/competitor_research.py` — **never a new
  silo**; that module is already where all four agents get market context.
- `core/competitor_research.py` — the shared "look before you generate" step, used by the
  media, website and ads agents through three LENSES (`media` / `website` / `ads`). One
  cached `claude_web_search_call` per (client, lens) — 14/30/7-day TTLs — so four agents
  don't buy the same research four times. **`seo_agent` deliberately does NOT use it**
  (it is tool-first: real SEMrush/Ahrefs data beats a web search); the dependency runs the
  other way — this module reads seo_agent's cached research for real competitor domains at
  zero paid units. Standing rules in every lens prompt: never invent metrics, inform never
  copy, say plainly when nothing was findable. The **media** lens also returns `EXAMPLES:`
  — real links to content working in the niche, which media_agent sends to the CLIENT
  (`send_trend_examples`). Those URLs are verified in code against what the search tool
  actually returned and dropped when they can't be, because a fabricated link is a broken
  promise to a paying client; zero surviving examples means nothing is sent.
  Two sources ground the media lens and both stay active: web search, and **real YouTube
  videos** (`youtube_service.search_videos`, plain `YOUTUBE_DATA_API_KEY`, no OAuth —
  media lens only, ≤2 searches per uncached pass, sharing YouTube's 10,000 units/day
  quota with uploads). TikTok's and Meta's APIs remain own-account-only and cannot see
  other creators. **No source exposes view counts, follower counts or trending rank** —
  never frame this as trend analytics. See `HANDOFF-social-trend-research.md`.
- `agents/` — one file per agent. `agents/_template_agent.py` is the canonical structure;
  use the `new-agent` skill when creating or modifying agents.
- `agents/onboarding_agent.py` — the sales pipeline (`run_full_onboarding`) AND the `PRICING`
  dict, which is the **single source of truth for all business/pricing rules** (setup-fee
  floors, per-platform 350 NIS monthly fees, SEO tool tiers, automation tier, benefit
  months). Change business rules there and in its prompts, nowhere else.
  Organic SEO (changed 2026-10-18) is **one fixed-price package, `ORGANIC_MONTHLY_PRICE_NIS`
  (500)** — never derived from a budget. The sales chat no longer asks for an organic budget
  (it asks for target search phrases, `organic_target_phrases`), and there is no minimum or
  tier. The professional SEO tool is **included in that price** (one shared, owner-controlled
  uallak vendor account) — unlike every other external tool, which each client pays on their
  own account. **SEO vendors are never named in client-facing text** (`qa_agent_content`
  scrubs them as a last guard). Fixed proposal terms (third-party subscriptions, organic tool
  included, monthly content allowance) are i18n text in the chat and the report email, not
  LLM output.
- `core/performance.py` — the client "ביצועים" page AND `channel_statuses()`, the ONE
  resolver of each channel's status (not_in_plan / not_connected / connected_no_data / active /
  needs_attention) — reuse it for alerts, never re-derive. Never a fake zero. Paid windows
  (7/30/90 + previous) come from one daily-series call per platform
  (`get_daily_metrics` in the ads agents, 1h cache). Leads are `client_leads`, never `leads`.
  Organic rows come from `core/rank_tracking.py`: provider interface with stubs only
  (`seo_pro` not implemented, `search_console` deferred — no new Google scope), target
  keywords table PROPOSED in `migrations/2026-10-18-client-target-keywords.sql` (not applied),
  internal soft cap `TARGET_KEYWORDS_SOFT_CAP` alerts admin only.
- `core/secrets_vault.py` — **every per-client credential is encrypted at rest** (AES-256-GCM,
  key `SECRETS_ENCRYPTION_KEY`, bound to client_id+platform so a ciphertext can't decrypt for
  another client). Written ONLY via `client_agent.add_account`/`upsert_account` (plaintext
  column always blanked; no key → the write fails, never plaintext). Decrypted ONLY by
  `get_client_secret()` (agents' `_get_connection` readers call `hydrate()`, which goes
  through it). `get_accounts()` returns rows WITHOUT secrets — screens see at most
  `secret_last4`. Client-paid vendor jobs (Higgsfield, HeyGen, ElevenLabs) take their key via
  `use_vendor_key()`, which aborts + alerts when the same key belongs to another client
  (except owner keys shared only between internal/billing-exempt accounts, logged). Data
  migration: `scripts/encrypt_client_secrets.py` (dry-run default).
- `core/client_deletion.py` — admin "מחיקת לקוח": preview → warnings → backup (no secrets) →
  typed name/id → `delete_client_full()`. Refuses internal (billing_exempt) and owner
  (`OWNER_EMAILS` + ADMIN_EMAIL/GMAIL_USER) accounts SERVER-side. Reuses
  `_disconnect_platform` for revokes; cost rows become anonymous monthly aggregates
  (`attribution='deleted_client'`) so totals don't move; resumable via the
  `app_settings` row `client_deletion:<id>`; the clients row goes LAST. Never touches
  external resources (site hosting, domains, Drive, ad accounts, PayPal) — it lists them.
  `delete_clients()` is the one engine (single delete = a chunk of one): lock per client →
  status `deleting` + marker → parallel revokes (5 s + 1 retry) BEFORE any connection row is
  deleted → batched statements per table → verification pass (re-delete + re-verify) → clients
  row last → id-only tombstone. The table list comes from the SCHEMA (`client_tables()`,
  PostgREST OpenAPI) plus `DATA_TABLES` as the known floor. `[client_delete_timing]` log
  lines carry ids + durations only.
- `core/google_data_purge.py` — the privacy policy's promise that **no Google-sourced data
  outlives a client**, for both ends: self-closure (`_offboard_client` → `end_of_client()` before
  the archive) and admin delete (`google_records` step + its verify). `archive_safe_export()` is
  what the Drive archive, the closure email and the admin pre-deletion backup carry: no Google Ads
  numbers, no Google account ids, no outbound messages, activity only from an ALLOWLIST
  (`ARCHIVE_ACTIVITY_AGENTS` / `ARCHIVE_ACTIVITY_ACTIONS`) — a new agent's rows stay out until
  added there. Also scrubs the two places with no client_id column: `weekly_reports`
  (report.clients[] + "<name>: ..." lines) and `alerts` (the "client {id}" convention; audit
  sources and alerts written after the run started stay). Leftovers block the clients-row delete.
  The Google Ads card's staff-access consent (`GOOGLE_ADS_STAFF_CONSENT`, required by
  `/api/oauth/google-ads/start`) is what the policy's human-access section rests on.
- `core/gtm_connection.py` — the Tag Manager card's truth: WHICH container is connected
  (name + GTM-XXXX; the client picks it after consent and can change it via
  `/api/gtm/containers` + `/api/gtm/select-container`, listed live through their own grant —
  no new consent, no new scope), whether the site's homepage loads that container
  (match / mismatch / no GTM / no site), and REAL token health from a live `containers.get`
  (`invalid_grant` → the card says "צריך לחבר מחדש", never "מחובר ✓"). Health lives in
  `app_settings` `gtm_token:<id>` (NOT client_activity — the client's feed shows unknown action
  types raw); client_deletion clears it. `configure_lead_conversion` refuses to publish on a
  mismatch or a dead grant. Control Room shows the same view + last OK time.
- `core/video_limits.py` — THE Drive → YouTube upload size limit (`VIDEO_UPLOAD_MAX_MB`, 512)
  and chunk size, read by client_state, agent_actions, verification_demo and
  `youtube_content_agent.publish` (the root guard). Uploads STREAM: `drive_service.read_range`
  reads one 8 MiB range at a time into `youtube_service.upload_video_stream`'s resumable session
  (Content-Range per chunk, resume from the server's offset on 308/5xx/dropped connection,
  bounded retries) — memory never grows with the file, and there is no privacy parameter
  (always Private). What bounds the size now is the ~300 s request timeout of a chat click;
  raising it further means moving the upload to a background job first.
- `core/google_ads_health.py` — the same token-health model for Google Ads: the token refresh
  raises `google_ads_service.GrantRevoked` on a 400 `invalid_grant`/`unauthorized_client` (code
  read from the body, never the token); the readers record it per client + grant
  (`app_settings` `gads_token:<id>`, grant = token fingerprint), stop calling Google for it
  (one re-probe per 6 h), answer `needs_reconnect` (Performance card needs_attention/reconnect,
  client_state fact, connect_google_ads card), and ONE plain admin alert per dead grant. A new
  grant (OAuth callback → `forget_grant`) clears it. Deletion and the deletion guard cover the row.
- `core/php_exposure.py` + `core/php_advisory.py` — **PHP2Shell incident** (CVE-2026-109618/
  109619, see `docs/INCIDENT-PHP2SHELL.md`). Our runtime has no PHP; the WordPress sites we build
  (InstaWP) and connect to do. `php_advisory` is the ONE place the fixed versions live.
  `inventory()` = client_accounts `wordpress` rows + `website_provisioned` + assessed/migration
  sites (never a secret, only `has_app_password`). The check is ONE ordinary anonymous HEAD (GET
  fallback) of the homepage reading `X-Powered-By`/`Server` — **never a probe or payload**; the
  `_fetch` seam refuses other methods. `patched` means the site REPORTS a fixed release (a claim,
  not proof — `verified_safe` is always false); vendor builds of old releases and hidden
  versions are `unknown`. Record in `app_settings` `php_exposure:<id>` (deletion clears it, the
  guard covers it); one admin alert per site per status change. Control Room panel (list +
  client), `client_state.site_security` for agents (ACCOUNT_STATE_RULE: never call a site safe),
  admin action `rotate_wordpress_password` (`website_agent.rotate_stored_password`: mint →
  verify → store → revoke old). InstaWP has no documented PHP-version API — manual runbook.
- `core/deletion_guard.py` — while a client is being deleted (and after), every Supabase
  POST/PATCH carrying that client_id is refused at the one HTTP choke point (a pre-send hook
  in `core/db_retry.py`); sessions/logins, the PayPal webhook and lead capture check it too.
- `core/bulk_delete.py` — admin bulk delete for clients (via `delete_clients`, one chunk
  per request, the chunk's clients together), sales leads (a lead linked to a client is protected), a client's own leads, and
  alerts (deletion audit entries are protected). Typed phrase "מחק N", cap
  `BULK_DELETE_MAX` (50), per-item deleted/failed/skipped, one `bulk_delete` audit alert per
  batch. The admin UI only ever batches rows that are currently rendered.
- `core/redaction.py` — the ONE "looks like a key/password" definition; `redact()` runs on
  every stored chat message (client chat, sales transcript + answers, admin agent chat) and
  inside `log_step`/`agent_alert`/`master_agent.alert`.
- Client ↔ sales lead is matched by **client_id only** (`lead_tracking.client_sales_lead`) —
  the email fallback is gone; a missing link alerts admin once, never guesses.
- Costs (`cost_tracker.record_cost`) always have an owner: client_id, `INTERNAL`, or the
  sales-chat lead via `cost_owner()` (moved to the client on conversion), plus a metering
  `cost_category` (chat/proposal/content/media/seo/infra). Admin view: Control Room list.
- `core/chat_guard.py` — runs before a client chat message is stored or sent to a model:
  key/password-looking messages are never stored, plus the per-client length and daily caps
  (`client_settings`: `chat_max_message_chars`, `chat_daily_message_cap`). Ordinary chat turns
  run on `claude_json.CHAT_TURN_MODEL` (cheaper); deliverables stay on `DEFAULT_MODEL`.
- `core/connection_help.py` — one config entry per connection; the dashboard's "עזרה"
  buttons render from ACTIVE entries, and the concierge may only give that entry's links
  (`strip_foreign_links`). Every URL there already existed in the code — never add a guessed
  one; leave `url` empty instead.
- `core/automation_service.py` — ManyChat / Make, the one integration that is deliberately
  NOT a self-service connect flow: no fixed price, no OAuth, no client-facing credential
  form. The client dashboard shows informational cards that route to WhatsApp or the general
  concierge; Johnny scopes and prices it by hand and stores the client's login in the admin
  drawer (a `client_accounts` row per vendor, same shape as the WordPress Application
  Password). `support_agent._AUTOMATION_RULE` must keep forbidding `upgrade_request` for
  these — the pricing brain would invent a number. See `HANDOFF-marketing-automation.md`.
- `agents/qa_agent.py` (numeric, no LLM) → `agents/qa_agent_content.py` (merged content +
  master review) run after `build_proposal`.
- `core/lead_tracking.py` — the sales chat's lead lifecycle and traffic-source
  attribution. A `leads` row is opened when someone LANDS on the chat (not when
  a proposal is built), which is what makes source, first-contact time and
  drop-off knowable. Also owns the outbound tagging scheme
  (`GOOGLE_ADS_FINAL_URL_SUFFIX` / `META_URL_TAGS`) that google_ads_agent and
  meta_ads_agent stamp on campaigns they create — read side and write side of
  attribution deliberately live in one file. `dropped_off` is DERIVED at read
  time from `last_activity_at`, never stored. See `HANDOFF-crm-leads.md`.
- `core/lead_volume.py` — per-client lead-volume PRICING TIERS (value-based, gates
  nothing) plus uallak's own Supabase footprint watch. The two are deliberately
  never joined. **`count_client_leads()` is a documented placeholder seam**: no
  table stores leads delivered *to* a client (`leads` is uallak's OWN funnel —
  one row per client, the conversation that sold them), so it approximates with
  Google/Meta ad conversions and always reports `complete: False`. Tier crossings
  alert admin only — nothing bills automatically. See `HANDOFF-lead-volume-tiers.md`.
- `core/control_room.py` — the admin "חדר בקרה" (own nav tab): triage list → per-client
  view (activity, organic-vs-paid, client-matched alerts, feedback, "needs your
  attention") + Johnny's private chat with that client's personas. Alerts have no
  client_id — matched on the "client {id}" text convention, so keep writing it. The chat
  lives in `admin_agent_chat` (migration `2026-10-13-admin-agent-chat.sql`) and must
  NEVER move to `client_communications`/`client_activity`: both reach the client (chat
  history + data export). Answers are grounded in a per-question data block with explicit
  `data_gaps`; read-only like the client personas. See `HANDOFF-control-room.md`.
- `core/client_state.py` — **the** read-only account snapshot every agent answers from
  (client concierge, every persona, the Control Room): connections + health, website tracking
  (container, site match, missing scopes, lead event live), campaigns, spend vs stated budget,
  leads/CRM, approvals, site, landing pages, media, site security (PHP2Shell, stored check only). Built ONLY from existing readers
  (`performance.channel_statuses`, `gtm_connection.connection_view`, …), cached per section
  (`SECTION_TTL`), failures never cached and never shown to a model — they become data topics
  (`failed_topics`) for the fixed line, or Control Room `data_gaps`. Agents must never answer
  "I can't see it / go check / I'll pass it on" for anything in it (`ACCOUNT_STATE_RULE`).
- `core/agent_actions.py` — the ONLY way an agent sets something in motion: model returns
  `proposed_action` → `propose()` validates against the `ACTIONS` whitelist (who may propose,
  `internal_only`, availability from client_state, every param) and stores a proposal + card →
  only a human click (`decide()`) runs it, re-checked from the STORED row, claimed by
  compare-and-set (double click = one run). Runners are the SAME functions the fallback
  buttons call (`core/verification_demo` for the Google demo, `configure_lead_conversion`,
  `decide_suggestion`, OAuth start routes). `approver` client = one click; admin = staff
  request in the Control Room. Table `agent_actions` (migrations/2026-10-21-agent-actions.sql)
  holds no Google data. Adding an action = one dict in `ACTIONS` + a test in
  `tests/test_agent_actions.py` (stdlib unittest, runnable without deps).
  `publish_youtube_from_media` is the first client-approved action that WRITES to Google: a
  video from the client's own Drive media folder (`drive_file_id` re-proven by drive_mirror's
  parents walk at proposal AND at approval; video MIME, within `core/video_limits`) →
  `youtube_content_agent.publish`,
  always Private (no privacy param exists on the path), max
  `youtube_content_agent.DAILY_UPLOADS_PER_CLIENT` (3) per client per UTC day — refused with its
  own plain line (`refusals`), no alert. The card names the file from Drive's metadata, never the
  model's words; the done line carries the YouTube Studio link.
- `agents/youtube_metadata.py` — the title, description (1-3 links to the client's OWN pages,
  tagged with `lead_tracking.tag_youtube_link`), 3-5 hashtags, tags, category and language that
  `publish_youtube_from_media`'s card shows and the click uploads. One entry point
  (`prepare_publish`, the action's `prepare` hook), seo_article's shape: ground (request, file
  name, client_knowledge + organic_target_phrases, CACHED research, live page inventory, at most
  one cached `search_videos`) → draft or ONE clarifying question → critique → one revise; code
  then enforces every limit and rule, and `revalidate_stored` re-checks the STORED proposal at
  the click. Client changes in chat re-propose (`change_request`, max 3 per proposal). The
  metadata is written from context ONLY — no frames, audio, transcript or video analysis.
  **"Watching the video" is a deliberate future premium-tier feature, not built — do not add
  frame extraction, ffmpeg, transcription or video analysis to this path by accident.**
- `agents/seo_article.py` — HOW articles are written (seo_agent.write_article keeps the
  policy gates and stays the one entry point): live site grounding (real pages/posts,
  tone samples, primary language, translation plugin), client_knowledge + CACHED research
  only, one web-search intent call, brief → draft → critique → revise. Code enforces links
  (site pages + the client's requested link only) and collects `[placeholder: ...]` for
  facts the data lacks — never invented. From chat it runs as a BackgroundTasks job; the
  outcome is posted into אורי's thread and the window polls for it (append-only).
- `core/client_settings.py` — per-client limits (weekly article cap, package check,
  repeat-topic block, scheduled "auto articles", draft revision cap), defaults by TIER
  (paying keeps every brake; billing_exempt gets none and no scheduled writing). One
  `app_settings` row per client (`client_settings:<id>`), no migration; edited in the
  Control Room. `admin_service.get_settings` skips those keys.
- `core/chat_fallback.py` — **no client-facing chat ever dead-ends on an error.** Two
  distinct cases: OUT OF SCOPE (model works; it answers in its own voice, flags
  `out_of_scope`, we alert) and TECHNICAL (something broke; FIXED per-persona,
  per-language reply, no codes/"system error", we alert). `support_agent`'s
  `answer_support_question` / `answer_persona_question` never raise and never return an
  empty reply — catch `Exception`, not `ClaudeJSONError` (API timeouts aren't JSON errors).
  Every chat alert goes through `alert_handoff()` (deduped via a `client_activity`
  `issue_key` row, ISSUE_DEDUP_DAYS-style; prospects dedupe in memory). Don't add a raw
  `agent_alert` or a generic "תקלה" line to a chat path. See `HANDOFF-graceful-chat.md`.
  **A failed data read never reaches the model** (no error blocks, no "unavailable"
  list, no chat_fallback rows in `recent_activity`). The model labels each message's
  `data_topic`, and code shows the fixed "can't pull that up" line only when that topic's
  read failed in the same turn. Don't reintroduce failure state as prompt context. See
  `HANDOFF-chat-data-availability.md`.
- `core/client_auth.py` — client credentials. Auth is **custom, not Supabase Auth**:
  `/api/checkout` creates the `clients` row (before PayPal), and every login ends in the
  HMAC `session` cookie from `core/session.py`. Three ways in: emailed 6-digit code
  (the original, always available), password (stdlib scrypt), Google sign-in
  (openid + userinfo.email only; signs in an EXISTING client by verified email, never
  creates one). **The FIRST Google sign-in to an account links nothing until the emailed
  login code is entered** (`google_callback_outcome` → `start_google_link` → POST
  `/api/oauth/google-login/verify-code` → `complete_google_link`): the pending step is a
  10-minute HttpOnly cookie (`session.create_google_link_token`, bound to the sign-in nonce
  cookie and to the exact `login_codes` row, which is what makes it single-use); same code
  expiry and attempt limit as code login. A linked `sub` signs in directly; a different `sub`
  is refused. Activity rows `google_link_code_sent` / `google_link_verified` (no code, ever)
  are hidden from the client feed. **A password is only ever set via a fresh emailed code**
  (`set_password_with_code`): post-payment setup, forgot-password and admin reset are
  that one path. Password change/reset bumps `clients.session_version`, which kills
  every other cookie. Admin "New Client" creates a **`billing_exempt`** account (no
  PayPal, excluded from MRR/counts/margins) whose services GRANT is an
  `account_created_by_admin` activity row that `client_journey._chosen_package` reads
  in place of a checkout package. Credential columns are stripped by
  `client_agent.public_client()` wherever a full row leaves the server. Needs
  `migrations/2026-10-13-client-auth.sql`. See `HANDOFF-client-signup.md`.
- `core/paypal_service.py` — **Sandbox base URL hardcoded**; not live.
- `core/drive_service.py` — Google Drive archive for offboarded clients (service account
  via `GOOGLE_SERVICE_ACCOUNT_JSON`, folder `DRIVE_ARCHIVE_FOLDER_ID`). Closure/transfer
  exports the full client record to Drive, then purges the live rows to a PII-stripped
  tombstone; offboarded clients are hard-locked out of login.
- `core/drive_mirror.py` + `core/drive_download.py` — the Media tab's live listing of
  the client's Drive folder, and its preview/direct-download proxy. A file id from the
  browser is authorised ONLY by walking Drive `parents` up to the client's root, fresh
  per request. **Videos never go through our server (no player, no download, any role);
  images/documents only up to `media_download_max_mb` (default 20)** — enforced in
  `drive_download.plan()` (OpenInDrive → 200 `open_in_drive` / 303 to Drive), not just
  the UI. **Inline only for raster images**: this origin carries the session cookie, so
  everything else is an attachment. Shared UI: `dashboard/assets/media-viewer.js`.
  See `HANDOFF-media-download.md`.
- `core/client_leads.py` — the leads a CLIENT receives (`client_leads` table), fed by the
  PUBLIC signed-token endpoint `POST /api/leads/capture/{token}` that a form on the client's
  own site posts to. Read the module docstring before touching it: this is NOT
  `core/lead_tracking.py`'s `leads`. On sites WE build, that form is injected into the
  contact page automatically at provision time (`website_agent.install_lead_capture_form`,
  see the website skill) — the client never handles the token or an embed snippet; the
  manual snippet is now only a labelled fallback for sites we don't manage.
  See `HANDOFF-client-dashboard-nav.md` and `HANDOFF-lead-capture-autowire.md`.
- **Existing-site path (`website_agent.assess_existing_site`)** — a client with a site
  gets ONE of two different offers, decided by automatic anonymous detection
  (`wordpress_service.detect_wordpress`): WordPress → **migration**, anything else
  *including unreachable* → **rebuild**. Uncertainty routes to rebuild on purpose.
  **Migration is a human runbook, not an API call**: InstaWP's migration is
  dashboard-driven and needs an interactive WP-admin authorization our backend cannot
  perform. The rebuild path reuses `request_self_provision` (entitlement + duplicate
  guards intact), never a second door into `provision_site`. All expectation-setting is
  FIXED Hebrew text in אורי's chat thread — it's a promise, so it must not drift.
  See `HANDOFF-existing-site.md`.
- **WordPress connect is ONE-CLICK by default** (`website_agent.start_authorize_connect` /
  `complete_authorize_connect`, `/api/website/authorize/{start,callback,rejected}`): WP
  core's `authorize-application.php` approval screen, discovered first via the anonymous
  REST index (`wordpress_service.discover_authorization`). The password arrives in a URL
  query string, so it is **rotated** immediately (mint fresh over REST, delete the
  URL-borne one) before `connect_site()` stores it exactly like a pasted one. The manual
  Application Password form (`/api/website/connect`) stays as the fallback. See
  `HANDOFF-wordpress-oneclick.md`.
- `core/crm_service.py` + `agents/crm_agent.py` — OPT-IN sync of `client_leads` into a
  CRM the CLIENT owns (HubSpot, Pipedrive). **A client with no CRM connected sees zero
  change**, and our copy of the lead is always the record: capture stores the row and
  answers the customer BEFORE any CRM call (BackgroundTasks), so a broken CRM can never
  delay a form submit or lose an enquiry. `crm_service.VENDORS` is the only place a vendor
  is named — adding one is a single dict entry, no endpoint or dashboard change. Zoho is
  deliberately excluded (OAuth-only, not a paste-a-key card). Credentials reuse
  `client_agent.upsert_account` with the vendor in `account_id`, so switching vendors
  replaces the row instead of orphaning a key. Needs `migrations/2026-08-03-crm-sync.sql`.
  See `.claude/skills/crm/SKILL.md` and `HANDOFF-external-crm.md`.
- `core/landing_pages.py` + `core/landing_domains.py` + `agents/landing_page_agent.py` —
  in-code LANDING pages (lightweight, one offer, one form), deliberately NOT the WordPress
  site `website_agent` builds: no shared table, renderer or hosting path. All clients' pages
  are served by ONE route on this app (`/lp/{client_slug}/{page_slug}`) — never a hosting
  project per page. 3 included per client, **enforced server-side**; a 4th is blocked and
  priced by a human, never by code. Content is STRUCTURED and escaped into a fixed template
  — raw HTML is never stored, because these serve from the same origin as the dashboard
  session cookie. See `.claude/skills/landing-pages/SKILL.md`.
- `core/operating_costs.py` — uallak's OWN company-wide spend ("what am I actually
  spending"), admin-only. Distinct from `client_costs` (per-client AI usage, which this
  READS and never writes) and from `budget_agent` (ONE client's picture). **No provider we
  use exposes a readable billing API**, so every line is labelled by how its number was
  obtained — `measured` (real token counts × list rates), `derived` (live site count ×
  known rate), `manual` (typed, with a last-confirmed date that ages), `none` (genuinely
  free). An unfilled manual row shows "not set", never ₪0. Adding a service = one dict in
  `SERVICES`; only `manual` rows are ever writable. Needs
  `migrations/2026-08-03-operating-costs.sql`; degrades honestly until it runs. See
  `HANDOFF-operating-costs.md`.
- `core/export_service.py` — .xlsx written with stdlib `zipfile` (no openpyxl), a
  print-styled HTML view the browser turns into a PDF (Hebrew needs an embedded font +
  bidi shaping the container lacks), and Google Doc HTML for `drive_service`. Adding an
  exportable dataset means one branch in `api_server._export_dataset`, nothing else.
- `core/tiktok_service.py` — TikTok asks for **`user.info.basic` + `video.upload` only**
  (Upload-to-Inbox = drafts the owner publishes; `video.publish` is Direct Post's scope and is
  NOT requested — `init_direct_post` is unreachable until a content audit). TikTok app review
  approves only scopes the demo shows (rejected 2026-10-29); review text + shot list in
  `docs/TIKTOK-REVIEW.md`. What each grant covers is recorded in `core/tiktok_grant`
  (`tiktok_grant:<id>`, from the callback and every token refresh); a grant without
  `video.upload` or dead → `needs_reconnect` everywhere and the `connect_tiktok` card. The
  client sends a video to their TikTok drafts with the agent_actions card
  `send_tiktok_to_inbox` (client-approved, ≤ 64 MB `video_limits.TIKTOK_INBOX_MAX_MB`, never
  Direct Post, no caption). `video.list`
  (engagement counts) sits behind ONE flag, `ENGAGEMENT_READS_ENABLED` (off): while off,
  `list_videos`/`query_videos` refuse before any HTTP, `tiktok_content_agent.get_engagement_summary`
  answers `engagement_available: False` with no TikTok call, and the Performance card /
  client_state carry note `engagement_counts_not_available_yet` — never `sync_failed`. Turning
  it on needs a Display API review + clients reconnecting. See the tiktok skill.
- **The public landing `/` is reviewed by platforms (Google, TikTok)**: it must stay readable
  without sign-in in English (i18n.js gives English to any browser that asks only for
  languages we don't carry), explain each service literally (Services section — keep it true
  to the code: TikTok is inbox upload, never paid ads or auto-posting), show the pricing
  STRUCTURE (no numbers — those live in `PRICING`), contact `johnny_support@uallak.com`, and
  footer links to `/terms/` and `/privacy/` (Hebrew: `/privacy/he/`) in every language.
- `dashboard/` — static HTML pages served by FastAPI mounts: landing `/`, chat `/chat/`,
  terms `/terms/`, privacy policy `/privacy/` (English, static, no JS — the URL on the Google
  OAuth consent screen; Hebrew twin at `/privacy/he/`; its Google-data sections must stay in step
  with `docs/GOOGLE-SCOPES.md`), login `/login`, client dashboard `/dashboard`, client profile `/profile`
  (picture, external ad-spend transparency report, logout, account closure/transfer),
  admin dashboard `/admin` (ADMIN_PASSWORD login → signed admin_session cookie; separate
  from the X-Admin-Key header that guards server-to-server /api endpoints).
- `dashboard/assets/` — mounted at `/assets`, shared by every page: `i18n.js` and the
  browser mark (`favicon.svg` + `favicon.png` + `apple-touch-icon.png`, the wordmark's
  lowercase "u" in `#FF4C1F` on `#0D0D0D`). All seven pages carry the same three `<link>`
  tags; the generated `/lp/` client landing pages deliberately do not — those are the
  CLIENT's brand, not ours.

## Conventions

- Model: `claude-sonnet-4-6` (default in `core/claude_json.py`), except ordinary chat turns,
  which use `CHAT_TURN_MODEL` (`claude-haiku-5-5`) at low effort.
- Client-facing text in Hebrew; code, logs, prompts in English.
- Secrets only via env vars, registered in `agents/keys_agent.py` `KEYS` so startup warns
  when one is missing. Never hardcode credentials (a Gmail app password leaked into git
  history once and had to be rotated).
- Prompts must state hard output-length limits — response length is the main latency driver.
  The full proposal pipeline has a < 2-minute target and is not there yet.
- Supabase tables: `clients`, `client_accounts`, `client_agents`, `client_activity`,
  `client_communications`, `client_suggestions`, `leads`, `lead_messages`,
  `client_lead_volume`, `client_leads`, `client_feedback`, `landing_pages`, `login_codes`,
  `alerts`, `client_costs`, `app_settings`, `weekly_reports`. **`leads` is uallak's own acquisition funnel,
  not leads delivered to clients** — a frequent and expensive misreading.
  `client_leads` (core/client_leads.py) is the other direction: people who
  contacted a CLIENT. The two never join.
- Schema changes are hand-applied in the Supabase SQL editor. Put the statements
  in `migrations/<date>-<what>.sql` first so the change is reviewable and
  repeatable — idempotent (`add column if not exists`), and say in the file what
  breaks if it hasn't been run yet.

## Known traps

- PayPal `401 invalid_client` after a deploy: check the PayPal developer dashboard first — a
  stale/deleted Sandbox app on PayPal's side caused this once; it wasn't a code bug.
- Many endpoints are `async def` but call long blocking sync code (LLM calls) — one proposal
  build can block the whole event loop. Prefer plain `def` for blocking endpoints.
- Empathy analysis runs ONCE early (intro only) and is deliberately reused — do not add a
  second full-conversation empathy call; that was removed on purpose for speed.
- **Never re-parent a DOM node from a `mousedown` handler.** `appendChild` on an
  already-attached node is a remove + re-insert, and a subtree that leaves the document
  between mousedown and mouseup gets no `click` event at all. This silently killed every
  button inside the client dashboard's agent chat windows (close / minimize / new chat /
  download / send) — see `focusWindow` in `dashboard/client/index.html`, which now raises a
  window with CSS `order` + `z-index` instead. Reorder with CSS, not with the DOM.
- `overflow` / `text-overflow: ellipsis` do **nothing** on an inline element. Several
  header labels are `<span>`s and need an explicit `display: block` to actually clip
  instead of spilling over the controls next to them.
