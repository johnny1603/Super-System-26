# No tool or vendor names in client-facing text (2026-11-16)

`core/vendor_names.py` is THE list of tools that run behind our service, with the plain words a
client reads instead:

| Kind | Tools on the list |
|---|---|
| AI video | the AI video tool, and the models behind it |
| AI image | image models |
| Voice | the voice tool |
| SEO | the professional SEO tools |
| Hosting | our site hosting |
| Automation | chat automation |
| Editing / music | video editing, the music library |

**Not vendors here:** the client's own platforms (Google, Google Ads, YouTube, Meta / Facebook /
Instagram, TikTok, WordPress, WhatsApp, Gmail). Naming those is how a client knows what to connect.

## Where it is enforced

**Static text.** Every client page's visible text and translation table, every client-side
script, every card text in `agent_actions.ACTIONS`, and the fixed texts in the code (home focus,
goals, SEO report, branding, content plan, feature list, chat fallbacks).
`tests/test_vendor_names.py` fails when a name appears there.

**Model-written text** (the code backstop, `scrub` / `scrub_auto`):
- the chat reply (`vendor_tools.scrub_vendor_names`);
- the idea cards (`/api/client/suggestions`): this was the "AI video" card that showed vendor
  names;
- every card a client reads (`agent_actions.card`, client actor only; staff see the real text);
- the proposal QA guard (`qa_agent_content`, in the proposal's own language; the service tokens
  are left alone);
- the stored weekly content plan;
- the morning briefing, the login moment, the feature announcement and the interview replies.

**The prompts.** The weekly ideas, the weekly content plan and the sales proposal now describe
the tools without naming them.

**Links are never touched**, because a sign-up URL carries the vendor's domain on purpose.

## Allow-list (a vendor may be named)

- **The connection help and sign-up flow** (`core/connection_help`, `core/connection_catalog`):
  the client opens an account on that vendor's own site.
- **The ownership / handover view** (`core/ownership`): those are the client's own accounts.
- **The legal pages** (`/terms/`, `/privacy/`).
- **Every admin page and admin-only script.**

## Fixed in this round

- The chat-automation card's label and its two prefilled messages.
- The feature list's automation entry. An older test that required the vendor name there was
  changed to the new rule.
- The proposal prompt's media-cost line, which told the model to name the video tool.
