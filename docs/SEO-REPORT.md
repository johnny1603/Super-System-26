# The organic SEO report (2026-11-15)

Code: `core/seo_report.py`, `core/seo_report_actions.py`, `core/open_pagerank.py`,
`core/dataforseo_connector.py`. Tests: `tests/test_seo_report.py`. Never run against a real
model, a real client or a real site.

## What the client gets

A report that reads like a proposal from an SEO consultant, in the client's language:

1. the bottom line;
2. where the site stands today (from the audit of their own site);
3. keyword opportunities (keyword, what people are looking for, what to do with it);
4. backlink opportunities matched to their industry. Per opportunity: the site, the type, a
   quality score, why it fits, what to do, and a ready outreach draft;
5. what to do next.

Opportunity types: directory or portal, mention without a link, guest post, partnership or
podcast, Google Business Profile.

Where: the Performance tab, box "Organic SEO report": open it as a page (print → PDF) or
download Word / Excel / CSV. In the chat: card `build_seo_report`.

## Rules enforced in code (after the model, `seo_report.clean`)

- **No paid links.** An opportunity whose text suggests buying, selling or trading links, a
  link exchange, a link scheme or a PBN is dropped (5 languages). The page carries a fixed line
  saying we never do that and why.
- **No promised ranking.** A line that promises a position is removed. The page carries a
  fixed "no one can promise a position" line.
- **No invented sites.** A site is listed only if the web search returned it. If the search
  fails, the report is still written with no site list, and says so.
- **No vendor names** in any client text.
- **Nothing is sent.** The outreach drafts are text. The report module has no send path.

## Free, and gated by the package

- The report is free. No price for a report exists anywhere in the code.
- Package covers organic → the click builds it. One fresh report per 7 days; the stored one is
  shown meanwhile.
- Package does not cover organic → no questions, no research, no model call. The client gets
  ONE card, `upgrade_to_organic`, that explains what the organic service includes and its fixed
  monthly price (from `PRICING`). The click prepares the normal upgrade offer through the
  existing upgrade pricing. Nothing is charged by the click.
- Package unreadable → refused (fail closed).

## The quality score (0-100)

- Without a key: the model's judgment of relevance and content quality only. Every row and
  the page say "judgment only".
- With `OPEN_PAGERANK_API_KEY`: half judgment, half a public domain-authority number.
  Only third-party domains that survived cleaning are sent. Never the client's domain or name.

### The key (owner only) {#the-key}

1. Sign in at `https://keywordseverywhere.com/account/sign-in` (Open PageRank moved there),
   open the "Open Page Rank" tab of the account and create a free key.
2. Paste it: Secret Manager → new secret → Cloud Run service `super-system` → env var
   `OPEN_PAGERANK_API_KEY` referencing the secret.

**Known limit, read from their documentation on 2026-11-15:** a free key works from ONE fixed
IPv4 address set on the account page. Cloud Run has no fixed outbound address. So a free key
may be refused (403) from our service. The report then stays judgment-only and the log says
why. Making it work would need either a paid key or a fixed outbound address for Cloud Run (a
Cloud Run / network change with its own monthly cost). Both are the owner's decision; neither
was done.

## DataForSEO: ready, disabled

`core/dataforseo_connector.py`, `ENABLED = False` (a constant, not a setting). No key exists,
no env var is registered, every function refuses before building a request, nothing calls it.

- **What it would add:** the client's real referring domains and their count, who links to
  competitors and not to the client, anchor texts, new and lost links. Today the report finds
  opportunities by web search and judgment and never states who links to whom.
- **What it costs:** pay-as-you-go with a minimum prepayment (about $50 when last known) and a
  per-request price on the Backlinks API; the Backlinks API may also carry its own monthly
  minimum. These numbers are from memory and were NOT checked against their price page —
  check it before deciding.
- **To enable:** see the steps in the file's docstring. It is a new paid service: owner's
  decision.

## Cost per report

One web-search call + one writing call on the heavy model. Recorded in `client_costs`
(action `seo_report`) and on the stored report (`cost_ils`). Not measured live yet.

## Storage

`app_settings` `seo_report:<id>` (latest report only). Cleared with the client. No migration.
