# Auto-wired lead capture — the client never touches embed code (2026-08-01)

## What was wrong

`POST /api/leads/capture/{token}` (`core/client_leads.py`) worked, but its only
install path was a client copying an HTML `<form>` snippet carrying their token
into their own site. That is not a real install path for a non-technical Israeli
SMB owner, so `client_leads` stayed empty for everyone and the dashboard's leads
section showed a permanently honest empty state. Meanwhile we already build and
hold admin credentials for these clients' WordPress sites — the form was ours to
wire all along.

## Investigation result: there was no contact-form mechanism at all

Checked rather than assumed, which changed the plan:

- **No form plugin, anywhere.** Nothing in `agents/` or `core/` installs,
  reads, writes or configures Contact Form 7, WPForms, Forminator, Fluent
  Forms, Ninja Forms, or an Elementor form. `install_seo_plugin` and
  `install_accessibility_plugin` are the only plugin installs in the codebase.
- **The only thing that ever touched "contact" was an existence check.**
  `run_standards_check` asserts a page matching `REQUIRED_PAGES["contact"]`
  ("contact", "צור קשר", "יצירת קשר", "צרו קשר") exists, report-only, never
  auto-created.
- **The master template's contact page is hand-built in InstaWP**, outside this
  repo, so whatever form markup it carries is not verifiable from code.

So there was no "webhook-on-submit option" to check for — no plugin to check it
on. That ruled out the plugin-bridge route the handoff asked us to consider
first.

## What was built: a plain HTML form injected into the contact page

`agents/website_agent.py`, new "Lead capture" section.

The capture endpoint was already written to accept a classic urlencoded
`<form method="post">` — it hand-parses the body specifically so that no JS and
no `python-multipart` dependency are needed. So the smallest thing that works is
to put exactly that form in the contact page's content through the existing
Phase-1 REST write path:

- **zero plugins** (a form plugin would cost one of `MAX_ACTIVE_PLUGINS`, a
  wordpress.org dependency, and a webhook add-on),
- **zero JS**, and
- **it fires GTM's built-in Form Submission trigger**, which is what
  `configure_lead_conversion` builds the GA4 `generate_lead` conversion on. An
  AJAX-submitting form builder would silently not.

### The pieces

| Piece | Why it is there |
|---|---|
| `install_lead_capture_form(client_id, page_id=0)` | Finds the contact page with the SAME `REQUIRED_PAGES["contact"]` keywords the standards check uses (published beats draft), then replaces-or-appends a marker-delimited block and writes through the **gated** `update_content` — the standing quality rules are not routed around. |
| `<!-- uallak-lead-capture:start/end -->` markers | A re-run REPLACES the block. Re-provision, a rotated token, or a second `populate_site` can never leave two forms on a page. |
| `_ensure_thanks_page()` | **Load-bearing, not decoration.** A classic form post navigates the browser to whatever the endpoint returns — without a `redirect` the customer lands on `{"success":true,"stored":true}`. A published `toda` page on the client's own site is a redirect target that passes the endpoint's same-host-https open-redirect guard, because it *is* the same host. Idempotent. |
| `verified_on_page` | Re-fetches the page **anonymously** after writing, same discipline as `install_tracking_tags`. `<form>` survives `wp_kses` only for a user with `unfiltered_html` (our provisioned sites use an admin — yes; an Editor on a client's own site — no). A 2xx on the REST write proves nothing. |
| `get_lead_capture_status()` / `lead_capture_summary()` | Live audit (admin + standards check) vs activity-log read (client dashboard). Split because the client's panel renders on a page load and must not make two HTTP calls to their site. |

### Per-client token (handoff item 3)

Already structurally correct and left that way: `create_lead_capture_token` is
HMAC-derived from `SESSION_SECRET_KEY` and carries the client_id, so
`_capture_url()` mints it per client on every call. Nothing is stored, shared or
hardcoded, and a token minted for client A verifies as nothing but client A.
Verification matches the client's **full** capture URL, so a page carrying
another client's token reports as a problem ("leads are landing in another
account") rather than as a success.

### Where it runs automatically

- `provision_site` — **before** `run_standards_check`. That check now reports an
  unwired form as an issue, and alerting about a gap two lines away from being
  closed is noise.
- `populate_site` — at the end, for sites we provisioned only. A populate run
  can introduce the REAL contact page over the template placeholder; the marker
  makes that a replace.
- `run_standards_check` — **report-only**, deliberately. Installing edits page
  content, and a standing scan that rewrites pages on a site the CLIENT
  connected is too much power for a check. Same split as the tracking tags.

## Dashboard (handoff item 4)

`GET /api/client/lead-capture` now also returns `auto_wired`, `site_connected`,
`managed_by_us`, `form_page_link`. The leads panel shows one of three states —
wired ("already connected, nothing to install"), pending, or manual — and the
raw snippet moved into a **collapsed `<details>` "מתקדם / למפתחים"** that is
hidden entirely for clients whose site we manage. All five languages.

## Where this is genuinely unbuildable (handoff item 5)

The manual snippet stays for these, labelled as the fallback rather than shown
to everyone:

1. **No site of ours at all** — Wix, Squarespace, a developer's custom build, or
   any site with no `wordpress` row. No REST surface to write to. This is the
   case the collapsed developer toggle exists for.
2. **`ERR_SITE_NOT_HTTPS`** — the redirect guard only accepts an https target,
   so on a plain-http site the customer would land on raw JSON. Refused rather
   than shipped. (Near-unreachable in practice: Application Passwords need HTTPS
   anyway.)
3. **`ERR_NO_CONTACT_PAGE`** — never auto-created, same rule as the standards
   check. Reported.
4. **`ERR_PAGE_QUALITY`** — the contact page's pre-existing HTML violates the
   standing quality rules, so the page cannot be written at all. Checked and
   reported separately from our own block, so it reads as the page's problem
   rather than a rejection of the form. **This is the one to watch on the master
   template** — if the hand-built צור קשר page is non-compliant, every provision
   fails here.
5. **`verified_on_page: False`** — saved but stripped (WP user lacks
   `unfiltered_html`) or the theme doesn't render page content as-is. Alerts,
   and the dashboard does NOT claim the client is wired.

## VERIFICATION STATUS

Written against the WP core REST behaviour this agent already relies on;
**never run against a live site** — this machine has no usable Python, and there
is no provisioned site to test on until the InstaWP master template exists.
Same caveat class as the widgets-REST tracking injection. The two things most
worth checking on the first real provision: that the master template's contact
page passes `content_quality_issues`, and that `verified_on_page` comes back
`True` (i.e. the theme renders raw page HTML and the admin user keeps
`unfiltered_html`).

## Follow-on

`core/lead_volume.py`'s `count_client_leads()` is still the documented
placeholder that approximates from ad conversions. Once a real client's form is
verified capturing, that seam is finally switchable — it was waiting on exactly
this.
