# מאיה and the competitors' public social presence (2026-11-16)

Code: `core/competitor_watch.py`, `core/competitor_watch_actions.py`, `youtube_service.public_channel_recent`.
Tests: `tests/test_competitor_watch.py`.

## What the client gets

- `track_competitor` adds a competitor the client named, with optionally one public link.
- `competitor_digest` is the weekly read: what works for them, what works less, and up to 5
  ORIGINAL ideas for this client to do better and more creatively.
- The latest read also feeds מאיה's weekly content plan (`media_agent.plan_week`, payload
  `competitor_watch`) as inspiration only.

## Only methods the platforms allow, and what each gives

| Platform | Method | What we get |
|----------|--------|-------------|
| YouTube | Data API on our project key, no client connection: channels.list → playlistItems.list → videos.list. 3 quota units per channel, never search.list. | Recent videos with their PUBLIC views / likes / comments. Measured. A hidden count stays empty, never 0. |
| Instagram | Business Discovery (Graph API) would give other Business/Creator accounts' public posts and like/comment counts. It needs `instagram_manage_insights`, which we do NOT request. | Not used. Impression from public web search only, said so. |
| Facebook pages | No API reads other pages' posts or numbers with our grants. | Public web search impression only. |
| TikTok | Our APIs are own-account only. | Public web search impression only. |
| Anything else | The existing competitor research (media lens, web search, links verified). | Observed, no numbers. |

We never scrape, never log in as anyone, and never use a paid data service.

## Never copy

The prompt forbids copying or closely imitating a competitor's caption, script, creative, slogan, logo or
trademark. Code then cleans the model's output:
- drops any line that quotes more than 12 words;
- removes any link that no source returned;
- scrubs vendor names.

## Decision for the owner (stop-and-ask)

A reliable read of competitors' Instagram (real public numbers) needs ONE new Meta scope,
`instagram_manage_insights`. It needs Meta App Review and every client reconnecting. It is not a paid
service, but it is a new scope, so it is the owner's decision; it is not requested here. Facebook and
TikTok would need a paid third-party data service, which we have not asked for.

## Storage

app_settings `competitor_watch:<id>` holds the watch list and the latest read. At most one fresh read a
week. It is cleared with the client.

## Not tested against a real API, model or client

- the YouTube calls (shape per the API docs);
- the quality of the read.
