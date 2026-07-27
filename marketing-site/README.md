# uallak's own marketing site — content payload

`uallak-pages.json` is the marketing content for uallak.com, in exactly the
shape `POST /api/website/populate` takes (`{client_id, items}`). How to load it
is Step 1.3 of `../HANDOFF-domain-split.md`.

Source of the copy: `dashboard/landing/index.html` — the landing page already
designed for the app. This is that same content, restructured from one long
scroll into the page set a marketing site is expected to have.

## The eight pages

| slug | title | what it is |
|---|---|---|
| `home` | העסק שלך יכול יותר | hero + the four pain profiles + before/after + the commitment |
| `how-it-works` | איך זה עובד | the five onboarding steps |
| `services` | מה המערכת עושה בשבילך | the eight automations |
| `for-who` | למי זה מתאים | the six industry profiles |
| `pricing` | מחירים ומסלולים | how a proposal is built — see below |
| `about` | אודות uallak | the origin story + the three pillars |
| `faq` | שאלות נפוצות | the five landing-page FAQs + two about the app/domain split |
| `contact` | צור קשר | WhatsApp, email, the chat, the client login |

## Two content decisions worth knowing about

**The pricing page carries no numbers.** All business and pricing rules live in
exactly one place — `PRICING` in `agents/onboarding_agent.py` — and a public
page with figures on it would be a second copy that silently goes stale the
first time the dict changes. The page explains how a proposal is built, what is
always disclosed up front, and the no-result-no-management-fee commitment, then
routes to the chat for an actual quote. If fixed public pricing is ever wanted,
that is a business decision that needs a real answer for how the two stay in
sync — not a copy-paste.

**Every page is `"status": "draft"`.** That is the website agent's standing rule
(a human publishes, same principle as campaigns created paused), and it applies
to our own site too. Review in wp-admin and publish. To publish on load
instead, change `"draft"` to `"publish"` in the JSON.

## Constraints the HTML has to keep meeting

`content_quality_issues()` gates every publish and rejects the whole item on a
violation. All eight pages currently pass: no `<h1>` (WordPress renders the
title as the page's single H1), headings start at `<h2>` and never skip a
level, every page has an `excerpt` (the meta-description surface), no `<img>`
without alt text, no unlabeled form fields. Keep that true when editing — the
contact page deliberately links to WhatsApp/email/chat rather than embedding a
form, which also avoids needing a form plugin on a fresh site.

Internal links point at `https://app.uallak.com/...` (chat, login, terms),
since the app no longer lives on the root domain.

## Required companion: `forward-campaign-params.html`

Because the marketing site and the chat are now on different domains, campaign
parameters (`utm_*`, `gclid`, `fbclid`, …) are dropped on the hop from
uallak.com to app.uallak.com unless something carries them across. That snippet
does it, and must be installed site-wide when the WordPress site goes up —
otherwise every paid lead lands in the CRM as "direct" and the whole
source-attribution feature reports nothing useful for ad traffic. Installation
options and a verification step are in the file's own comment header.
