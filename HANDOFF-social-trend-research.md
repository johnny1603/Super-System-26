# HANDOFF — social trend research → client scripts → reference links in chat

## ⚠ Read this first: what can actually see other people's content today

The brief asked to "scan currently-trending creators/accounts in the client's
niche" and to report what is feasible with **today's** access before building.
Every integration we own was checked. The finding:

> **Every social API this system holds is scoped to the client's OWN account.
> Not one of them can see another creator's content.** The only thing that can
> look outward today is the Anthropic server-side web search we already pay for.

| Source | Can it discover other creators? | Status |
|---|---|---|
| **Anthropic web search** (`claude_web_search_call`) | **Yes** | **Works today**, already integrated, already billed per client. This is what got built on. |
| TikTok Content Posting API (`core/tiktok_service.py`) | No | `video.list`/`video.query` are the connected account's OWN videos. Scopes are `user.info.basic`, `video.publish`, `video.list`. Nothing outward-facing exists in this product. |
| TikTok **Research API** (the one that could do this properly) | Yes, in principle | **Unobtainable.** Gated to academic/non-profit research; not realistically available to a commercial SaaS. Already documented in the tiktok skill as a genuine platform gap. |
| TikTok **Creative Center / Marketing API** (trending sounds, top ads, niche breakdowns — the best trend source that exists) | Yes | **DORMANT — blocked exactly like ad-campaign research.** Needs a TikTok Business Center account + its own app registration and review. The ads account is still pending approval. |
| Meta Graph API (`core/meta_service.py`) | No, as wired | `business_discovery` (public data on another IG Business account by username) is a real endpoint we do NOT use. It needs Instagram scopes at **Advanced Access** — App Review + Business Verification. We are on **Limited Access**, which only works on assets we admin. Dormant. |
| YouTube Data API (`core/youtube_service.py`) | Yes, in principle | `search.list` (public, `order=viewCount`, `regionCode=IL`) is genuine niche discovery and costs **zero money** — 100 quota units of the free 10k/day. But `_get()` authenticates with a **client's** refresh token, and YouTube consent is still Google-verification-gated to test users. Dormant per client. See "If you want to unblock one thing" below. |
| Higgsfield (`core/media_gen_service.py`) | No | Generation only. No trend/discovery surface in the Cloud API. |

**So: this feature is web-search-powered, and will stay that way until a social
account/approval lands.** That is a real ceiling, not a placeholder — see
"What is genuinely weaker than a real API" before promising a client more.

## What was built

### 1. Research: the media lens now returns real links (no parallel path)

Per the brief, **nothing new researches anything**. `core/competitor_research.py`'s
existing `media` lens gained one section:

```
EXAMPLES: creator or business name | full URL | one clause on what makes it work
```

Same lens, same prompt, same **14-day per-client cache**, same single paid
research pass. A client generating ten assets and receiving a weekly plan still
buys research once a fortnight. Adding a `"social_trends"` lens would have
bought the same searches twice — that is why this is a section, not a lens.

### 2. Links are verified in code, because a link is a promise

An invented URL sent to a paying client as "here's how others do it well" is
worse than sending nothing. Prompt rules alone don't prevent that, so:

- `claude_web_search_call(..., with_sources=True)` now also returns **the URLs
  the search tool actually returned** (from `web_search_tool_result` blocks and
  text citations). Default `False` — every existing caller is untouched.
- `_strip_unsourced_examples()` drops any EXAMPLES line whose links don't match
  a real search result, **before the summary is cached**. So the cache, the
  generation prompts and the client only ever hold links that demonstrably exist.
- Matching is one-directional on purpose: a creator's **profile** URL is accepted
  when search returned a page underneath it, but a URL *longer* than anything
  search returned is rejected — that is exactly what a guessed video id looks like.

**Expect this to drop lines, sometimes all of them.** That is the mechanism
working. Zero verified examples → nothing is sent at all.

### 3. Findings → the script/content plan

Three places now read the same cached lens (the first two already did):

- `_craft_prompt()` — every generated image/video (unchanged)
- `_checkin_for_client()` — the Saturday weekly plan (unchanged)
- **`create_filming_kit()` — new.** The self-filming script is content too, so
  it now gets the niche evidence: hook, pacing and shot list are shaped by what
  visibly works, preferring the lens's GAP. Same "inform, never copy" clause,
  plus an explicit ban on naming a competitor in words the owner will speak.

### 4. Delivery: ליאור's own chat thread, with the links

`media_agent.send_trend_examples(client_id)`:

- reads `competitor_research.media_examples()` (cached lens → verified rows)
- skips any URL already sent in the last **30 days** (the research cache outlives
  the weekly cadence — without this the same three links would arrive twice a
  fortnight)
- writes the Hebrew prose with an LLM in the **media persona's real voice**,
  loaded from `support_agent.PERSONAS["media"]` (ליאור) — not a second cast, not
  a generic system message
- **the model never writes a URL.** It writes the intro, one note per example and
  a closing line; the URLs are pasted in by code from the verified rows, so a
  mangled or shortened link is impossible
- posts to `persona_channel("media")` → `dashboard_chat:media`, which is ליאור's
  own thread in the existing chat window
- logs `media_trend_examples_sent` with the URLs (that row is also the dedup source)

It fires automatically at the end of the **Saturday check-in**, right after the
plan is stored — the plan says what we'll make, the examples show what already
works. Fully swallowed in a `try/except`: the sacred run must never fail over a
bonus message. Also triggerable manually: `POST /api/media/trend-examples`
(admin, `{"client_id": N}`).

`sent: false` is a **successful** response. It means nothing verifiable was
found, or everything found was already sent. Nothing is ever padded.

### 5. The client actually sees it (dashboard)

A specialist posting into their own thread was invisible — the client isn't
watching five windows.

- `GET /api/client-chat/unread` — newest outbound timestamp per persona thread.
- The chat FAB shows a dot; the launcher list shows which agent it came from.
  "Seen" lives in `localStorage` per thread, so **no schema change**; worst case
  is a dot reappearing on a new device.
- Fixed alongside: the login-moment's unread class was being applied only to the
  launcher popover, which is hidden until opened — it now marks the FAB too.
- Older-thread history rendered with `textContent`, so links in reloaded history
  were dead text. It now uses the same `linkify()` (escape-then-link) the current
  thread already used.

## What is genuinely weaker than a real API (say this out loud to a client)

1. **No view counts, no follower counts, no "trending this week".** Web search
   cannot see them and the lens prompt bans inventing them. What we send is
   "this is working in your space", never a ranked chart. Do not let this feature
   get sold as trend analytics.
2. **Recency is whatever search surfaced**, not a live feed. A month-old great
   video and a yesterday-viral one look the same to us.
3. **Coverage is thin for narrow Israeli niches.** Hebrew-first search helps
   (`user_location` is already IL), but expect empty results for a very local
   trade — and expect an empty result to send nothing.

## If you want to unblock one thing, unblock this

**A YouTube Data API key** (`search.list` + `videos.list`) is by far the cheapest
real upgrade: genuinely public discovery, no monetary cost, ~100 searches/day
inside the free quota, no OAuth and no platform review — the current blocker is
only that `youtube_service._get()` authenticates as a client. It would need a new
`YOUTUBE_API_KEY` in `keys_agent.KEYS` and a key-authenticated `_get` variant.
Deliberately **not** built here: the brief asked to report new integrations
rather than assume them.

Ranked after that: **TikTok Creative Center** (the best content available for
this, blocked on the same pending ads account as ad-campaign research), then
**Instagram `business_discovery`** (blocked on Advanced Access).

When any of them lands, it feeds `competitor_research`'s media lens as another
grounding source — the same way `seo_agent`'s paid-tool competitor domains
already do, at zero extra research cost. **Do not build it as a new agent or a
new lens.**

## Files touched

- `core/claude_json.py` — `with_sources` on `claude_web_search_call`, `_sourced_urls`
- `core/competitor_research.py` — EXAMPLES section, link verification, `parse_examples`, `media_examples`
- `agents/media_agent.py` — `send_trend_examples`, research-informed filming kit, Saturday hook
- `core/api_server.py` — `POST /api/media/trend-examples`, `GET /api/client-chat/unread`
- `dashboard/client/index.html` — unread dots, linkified history, new activity label (5 languages)
- `.claude/skills/media/SKILL.md`, `CLAUDE.md`

## Not verified live

The link-verification and parsing logic was exercised against 21 cases (invented
video ids on real hosts, ancestor profile URLs, tracking params, http/https, old
cached research with no EXAMPLES section, "none found") — ported to JS to run,
since this machine has no Python. **Not yet run against a real web search**, so
the open question on first real use is how many EXAMPLES lines survive
verification in practice. If it is routinely zero, the fix is the lens prompt
(tell it to search for specific posts, not round-up articles), not the verifier.
