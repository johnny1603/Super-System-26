# HANDOFF — existing website: migrate or rebuild

## ⚠ The investigation the brief asked for: InstaWP cannot migrate a site for us

**Our InstaWP integration has never had a migration path.**
`core/instawp_service.py` is create-from-template, task status, delete — and
InstaWP's v2 API exposes no migration endpoint at all.

InstaWP's actual migration product is **InstaMigrate**, and it is
**dashboard-driven and interactive**: you enter the source URL, are **redirected
to log in with WordPress admin credentials on that site and approve the
connection**, then repeat for the destination. That authorization step cannot be
performed by our backend, and no documented endpoint takes a URL and migrates it.

The InstaMigrate **plugin** *does* expose REST primitives — `/db/export`,
`/db/import` (with `source_url` for server-to-server pulls), `/files/archive`,
`/files/download`, `/files/extract`, `/files/search-replace`, `/disk-probe` —
authenticated by `X-Insta-Key` **or a WordPress Application Password**, which is
precisely the credential our connect flow already collects. So a fully automated
migration is *technically* reachable.

**It is deliberately not built**, and I'd push back on building it later without
a strong reason:

- it requires installing a plugin whose own documentation says to delete it
  immediately after use, on the client's **live business site**;
- **the destination is permanently overwritten, with no undo**;
- it is a multi-step, large-payload sequence with no transactional safety, and
  nothing about it can be tested before it runs on real client data.

That is the same call the codebase already makes for PAUSED campaigns, draft
posts and Drive-review media: **a human makes the irreversible tap.** So
everything up to that tap is automated, and the tap is Johnny's.

## What was built

### 1. Automatic detection — the client never has to know

`wordpress_service.detect_wordpress(url)` — anonymous, no credentials, never
raises. Signals in confidence order:

| confidence | signal |
|---|---|
| `certain` | `/wp-json/` root exposes the `wp/v2` namespace — nothing else produces that |
| `likely` | generator meta tag, `api.w.org` link relation (HTML **or** `Link` header), `wp-content` / `wp-includes` asset paths |
| `unlikely` | page loaded, none of the above |
| `unknown` | site could not be loaded at all (DNS, TLS, firewall, bot-blocking) |

**`unknown` is never collapsed into "not WordPress"** — they are different facts
and the client is told which one applies.

### 2. Two paths, and uncertainty routes to the safe one

`certain` / `likely` → **migrate**. Everything else, including unreachable →
**rebuild**. A wrong "yes" promises a migration that cannot happen; a wrong "no"
only offers a rebuild the client can decline. A `likely` verdict carries an
explicit hedge in the client's message ("we'll confirm before starting").

`request_migration()` refuses outright if the assessed path was `rebuild` —
the endpoint must not quietly contradict what the client was just told.

### 3. Rebuild reuses the provisioning gate, it doesn't bypass it

`start_rebuild_from_existing()` captures the old site's public title,
description and headings (shallow, public-only — never an attempted content
extraction) as `website_rebuild_reference`, then calls
**`request_self_provision` unchanged**. The entitlement check
(`_package_includes_hosting`), the duplicate guard and the billable-trigger
audit all still apply. Deliberately not a second door into `provision_site` —
that is exactly the kind of duplicate money-spending path that drifts.

The reference then feeds `research_site_landscape`'s build brief as
`existing_site_reference`, with the prompt told it is **reference, not a spec**:
carry across what the business IS, never reproduce the old layout or wording.

### 4. Expectation-setting in chat, in fixed wording

All explanation goes into **אורי's own thread** (`dashboard_chat:website`), per
the brief. It is **fixed Hebrew text, not an LLM paraphrase** — deliberate: this
is a promise about what the client will and will not get, and that wording must
not drift between runs. An LLM could soften "this is not a transfer" into
something misleading, which is the exact failure this feature exists to avoid.

The assessment is also added to `_website_reads`, so אורי answers follow-ups
accurately from what was actually detected — **fixed promise, conversational
follow-up**. His `data_notes` now forbid calling a rebuild a migration, implying
the design will look the same, or promising content moves automatically.

The migration message states plainly: 1-3 business days, human-assisted, admin
access needed, **and their existing site keeps running untouched until they
approve the copy**.

### 5. Dashboard

A third button on the existing site card — "יש לי אתר קיים — בדקו אותו" — takes a
URL, shows a one-line verdict plus the single appropriate CTA, and **opens
אורי's chat window** so the client reads the real explanation there rather than
on a card. The rebuild button lands in the same in-progress/polling state as a
plain new build. Five languages, per the i18n skill.

## Files

- `core/wordpress_service.py` — `detect_wordpress`, `public_page_summary`
- `agents/website_agent.py` — `assess_existing_site`, `latest_assessment`,
  `request_migration`, `start_rebuild_from_existing`, `_rebuild_reference`;
  build-brief prompt takes `existing_site_reference`
- `agents/support_agent.py` — assessment in `_website_reads`, migrate-vs-rebuild
  rules in אורי's `data_notes`
- `core/api_server.py` — `/api/client/website/assess`, `/migrate-request`, `/rebuild`
- `dashboard/client/index.html` — the third path on the site card
- `.claude/skills/website/SKILL.md`

No migration, no new env vars, no new tables — assessments and references are
`client_activity` rows.

## The migration runbook (what the alert tells Johnny)

1. Provision the destination site (`POST /api/website/provision`) if none exists.
2. In the **InstaWP dashboard**, run InstaMigrate: source URL → authorise with
   the client's WP admin login → destination site → authorise.
3. Confirm the migrated copy **with the client before any DNS change**.

Note the destination is permanently overwritten.

## Not verified live

No Python here, so nothing was executed and **no real site has been probed**.
The detection signal logic was ported to JS and run against 21 realistic page
shapes — open REST API, REST disabled but WP markers present, `api.w.org` in a
`Link` header only, Wix, fully unreachable, a non-WP JSON API answering on
`/wp-json/`, a broken homepage, and a site merely *mentioning* WordPress in prose
(correctly not a signal). All pass; Python was structurally checked.

**First real use should confirm**: a known WordPress site returns `certain`; a
known Wix/Squarespace site returns `unlikely` and offers only a rebuild; a site
behind Cloudflare bot protection returns `unknown` rather than a false negative
(if bot-blocking turns out to be common, the fix is a `Link`-header-only HEAD
probe before the HTML fetch, not loosening the signals).

## Deferred

Automated migration via the InstaMigrate plugin primitives (above — needs a real
decision about running an irreversible sequence on a live site, plus a staging
rehearsal); migrating a WordPress site *we* don't host into an existing site we
do; carrying media/content across on the rebuild path (currently a human step,
and the client is told so); detecting other CMSs specifically (we only answer
"WordPress or not" — a Wix site and a bespoke React site get the same rebuild
offer, which is the same honest answer).
