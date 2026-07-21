---
name: i18n
description: How multi-language support works in uallak — chat language matching via LANGUAGE_RULE in the agents, and UI localization via the /assets/i18n.js engine with per-page string tables and RTL/LTR switching. Use when localizing any dashboard page, adding a language, or touching client-facing prompt language rules.
---

# Multi-language support (he / en / fr / ar / ru)

## Two independent layers

1. **Chat language matching (LLM level, DONE for both chats):** the model
   detects the client's language from their own messages and replies in kind.
   No detection call, no stored preference — it's a prompt rule.
2. **UI localization (string tables + switcher, IN PROGRESS):** static page
   chrome translated via `/assets/i18n.js`. Login page is fully localized and
   is the reference implementation. Rollout order and status: see "Rollout".

## Layer 1 — chat prompts

- `agents/onboarding_agent.py` defines **`LANGUAGE_RULE`** — the single shared
  block (supported languages, Hebrew default, "adapt LANGUAGE ONLY — substance
  rules unchanged, currency stays NIS, Hebrew examples show style not literal
  text"). It is appended/interpolated into every client-facing prompt:
  dynamic questions, `build_proposal`, `handle_objection`, `get_reaction`,
  and (imported) `support_agent`'s SYSTEM + SEARCH_SYSTEM.
- `qa_agent_content.py` has a PRESERVE-language guard so review never
  translates a proposal back to Hebrew.
- When adding a client-facing prompt anywhere: append `LANGUAGE_RULE`, don't
  write a new language instruction.
- **RTL in chat UIs:** every dynamic message bubble/input uses `dir="auto"`
  (browser picks direction from the first strong character). Keep this on any
  new message-rendering code path.

## Layer 2 — UI engine (`dashboard/assets/i18n.js`, mounted at `/assets`)

- Engine only — **string tables live in each page** (locality), passed to
  `uallakI18n.init(TABLE)` after the DOM exists.
- Table shape: `{ key: { he, en, fr, ar, ru } }`; `{name}` placeholders via
  `t('key', {name: value})`. **Hebrew is source-of-truth and the fallback**
  for missing keys — partial translation degrades to Hebrew, never to raw keys.
- Static markup: `data-i18n="key"` (textContent), `data-i18n-placeholder`,
  `data-i18n-title`; `_page_title` key drives `document.title`.
- JS-generated strings: `uallakI18n.t(...)`; re-render on switch via
  `uallakI18n.onChange(fn)` if the page caches rendered strings.
- Direction: `setLanguage` flips `<html dir>`/`<html lang>` (he/ar → rtl).
  Force `dir="ltr"` on inherently-LTR inputs (emails, URLs).
- Switcher: `uallakI18n.mountSwitcher(containerEl)` — a settings-style round
  flag badge (2026-07-21 redesign, replacing the old plain-text `<select>`)
  showing the CURRENT language; click opens a small dropdown (flag + label
  per option). Mounted on every page that calls it: login, profile, the
  client dashboard's topbar, the sales-chat page, landing, terms. One
  redesign in the shared engine reaches all of them — never restyle this
  per-page; a page's own `.lang-switcher` CSS class is dead now (removed
  everywhere it existed) since the control no longer uses that class name.
  Flags: `FLAGS` in i18n.js. **Arabic deliberately uses Israel's flag 🇮🇱, not
  a generic Arabic-country flag** — these are Israeli Arabic-speaking
  clients, not a foreign audience — paired with the menu label "ערבית"
  (`MENU_LABELS.ar`, Hebrew word for Arabic, not "العربية") so the option
  reads unambiguously regardless of which language is currently on screen.
  Choice persists in localStorage `uallak_lang` across pages.
- Fonts: Heebo covers Hebrew+Latin; Arabic/Cyrillic fall back to system
  sans-serif. Good enough for v1; a Noto addition is a future polish item.

## Rollout (phased — deliberate scope decision)

- ✅ **v1:** both chats' language matching + RTL bubbles; engine; login page.
- ✅ **v2 (2026-07-18):** dashboard/client (incl. activity labels as
  `act_<action_type>` keys in DASH_I18N + tour + chat concierge),
  dashboard/profile, and the sales-chat page chrome — BASE_QUESTIONS +
  conditional questions translated via `Q_I18N` (Hebrew stays canonical in
  the question defs; `localizeQuestion()` applies translations).
- ✅ **v3 (2026-07-21):** landing page (full marketing copy, all sections);
  terms page (see below — translated WITH an AI-disclosure, not blocked on
  legal review); stored email-language preference; the server error-code
  pattern (see both below). All four were the explicit business-decision
  items flagged in v2 — none remain blocking.
- ✅ **v3.1 (2026-07-21):** switcher redesign (flag badge, see Layer 2 above)
  + confirmed it's genuinely mounted on every page, including the client
  dashboard's topbar (it already was — just easy to miss as a plain
  `<select>`; the flag badge fixes that visibility problem directly).
- **Not planned:** dashboard/admin (Johnny reads Hebrew).

## Terms page — translated with an explicit AI-disclosure (business decision, 2026-07-21)

`dashboard/terms/index.html` is now fully localized (all 15 sections), but
carries a `.translation-notice` box shown on every NON-Hebrew language
(hidden on `he`, toggled by `uallakI18n.onChange`) stating: the page was
AI-translated, the **Hebrew version is binding/authoritative** in case of
any discrepancy, and the client can contact the team for clarification.
This was a deliberate call to ship real translations now rather than block
on formal legal sign-off — if that sign-off later happens, the notice can
be softened or removed, but don't remove it without that review.

## Stored client language (emails — business decision, 2026-07-21)

Unlike chat (detects language live from the message) and the UI engine
(reads a switcher choice each page load), outbound emails have no live
signal to detect from — they need a STORED preference. `clients.language`
(he/en/fr/ar/ru, default `he`) is:
- **Captured at checkout**: the sales-chat page sends its active
  `uallakI18n.current()` in the `/api/checkout` body → `create_client(...,
  language=...)`.
- **Kept in sync afterward**: the profile page's language switcher
  fire-and-forgets a `POST /api/client/profile {language}` on every change
  (`syncLanguagePreference()`, same one-tap-preference pattern as
  `owner_gender`) — so a client who switches later doesn't get stuck with
  their signup-time language for emails.
- **Used by** `core/email_service.py`'s `_lang_of(client_id, language)` for
  every CLIENT-facing email (proposal report, payment confirmation, login
  code, sales-alert celebration, account closed/transferred). Each has its
  own small string table + `_tr()` (same `{name}`-placeholder convention as
  the JS engine) — the HTML STRUCTURE stays ONE shared template per
  function, only the strings swap, so future edits don't need repeating 5x.
- **NOT used by** `send_admin_alert` or the weekly Google Ads/Meta platform
  digests — those go to `ADMIN_EMAIL` (Johnny), who reads Hebrew, same rule
  as the admin dashboard. Don't localize those.
- **Known gap, coded defensively**: `clients.language` doesn't exist in
  Supabase yet. `create_client` and `POST /api/client/profile` both try the
  insert/update WITH `language` first and retry WITHOUT it on failure
  (never breaking checkout/profile updates) — add a nullable `language text
  default 'he'` column whenever convenient to make the stored preference
  actually persist instead of silently no-oping.

## Server error-code pattern (API errors — business decision, 2026-07-21)

Client-facing API failures return `HTTPException(status_code=..., detail=
{"code": "ERR_SOME_CODE"})` — a structured code, never raw Hebrew prose —
so the frontend can translate it into the current UI language instead of
displaying server text verbatim. `uallakI18n.errorText(body, fallbackKey)`
(in the shared engine) does the lookup: takes a parsed fetch response body,
lowercases `body.detail.code` (e.g. `ERR_NOT_CONNECTED` → `err_not_connected`)
and looks for that key in the PAGE's own table; falls back to `fallbackKey`
when the code is missing/unmapped. Never reads `body.detail` directly in
page code — that was the actual bug found and fixed (`dashboard/client/
index.html`'s upgrade-confirm handler and `dashboard/profile/index.html`'s
offboard/media-folder handlers all used to do `body.detail ||
t('some_fallback')`, which showed raw Hebrew straight through if the server
returned it, regardless of the client's chosen language).

Converted endpoints: login verify-code, package upgrade (3 codes),
disconnect (2 codes), close/transfer confirm-phrase (2 codes), media folder
(2 codes) — see `core/api_server.py`. `admin_login`'s "סיסמה שגויה" was
LEFT AS Hebrew prose on purpose (admin-only, Johnny reads Hebrew, same rule
as the admin dashboard) — don't convert it, that would be over-engineering
a surface this rule explicitly excludes.

If you add a new client-facing failure path: raise with a `{"code": ...}`
detail, add the matching `err_<lowercased_code>` key to the calling page's
own I18N table, and call `uallakI18n.errorText(body, 'some_generic_fallback')`
in the catch/failure branch — never `body.detail` directly.

## Critical patterns added in v2 (keep these invariants)

- **Sales-chat branching is by option INDEX** (`answerIdx` +
  `applyConditionalLogic(questionId, optIdx)`) — never compare translated
  option TEXT. Translations must preserve option order.
- The "other/something else" option is detected via `isOtherOption()`'s
  multilingual prefix list — extend it if a language is added.
- **Offboarding confirm phrases** exist per language on BOTH sides:
  `PHRASES` in profile page ⟷ `CLOSE/TRANSFER_CONFIRM_PHRASES` in
  api_server (lowercase match). Keep the two lists in sync.
- Elements whose text is set dynamically after connect (e.g. the connection
  cards) must `removeAttribute('data-i18n')` or applyDom will reset them on
  the next language switch.
- Language switch re-renders JS-built areas via `uallakI18n.onChange`
  re-running the load functions; the welcome tour auto-open is guarded by a
  once-per-pageload flag so a switch can't re-trigger it.
- All four former "business decisions, not engineering" items (terms page,
  transactional email language, server error codes, landing page copy) were
  resolved 2026-07-21 — see the dedicated sections above.

## Gotchas

- The sales-chat page's BASE questions are hardcoded Hebrew in its JS — the
  LLM's dynamic questions match the client's language but the scripted parts
  don't, until that page's chrome is localized.
- `_bare_path_redirect`/mount rules apply to `/assets` like any mount — it's
  registered before the root catch-all in api_server.
- Any NEW server error path must use the `{"code": "ERR_X"}` detail pattern
  from day one — retrofitting a raw-Hebrew-string endpoint later is exactly
  how the three leaks above happened (a page's failure branch reads
  `body.detail` once, and it quietly bypasses the whole i18n system).
