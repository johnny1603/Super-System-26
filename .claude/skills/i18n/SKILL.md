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
- Switcher: `uallakI18n.mountSwitcher(containerEl)` (native-name `<select>`);
  choice persists in localStorage `uallak_lang` across pages.
- Fonts: Heebo covers Hebrew+Latin; Arabic/Cyrillic fall back to system
  sans-serif. Good enough for v1; a Noto addition is a future polish item.

## Rollout (phased — deliberate scope decision)

- ✅ **v1 (done):** both chats' language matching + RTL bubbles; engine;
  login page fully localized (the pattern proof).
- **Next, in value order:** dashboard/client + dashboard/profile (the daily
  client surfaces — biggest string tables, includes ACTIVITY_LABELS and
  server-notice strings), then dashboard/onboarding page chrome (base
  questions + buttons are hardcoded Hebrew in the page JS), then landing.
- **Not planned:** dashboard/admin (Johnny reads Hebrew).
- **Business decisions, not engineering:** terms page (legal text needs real
  translation sign-off), transactional emails' language (currently Hebrew),
  server-side Hebrew strings in API error `detail`s.

## Gotchas

- Server responses (API `detail` errors, notice texts) are still Hebrew —
  localizing them needs an error-code pattern, not string matching.
- The sales-chat page's BASE questions are hardcoded Hebrew in its JS — the
  LLM's dynamic questions match the client's language but the scripted parts
  don't, until that page's chrome is localized.
- `_bare_path_redirect`/mount rules apply to `/assets` like any mount — it's
  registered before the root catch-all in api_server.
