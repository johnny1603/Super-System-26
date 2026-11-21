# מאיה and ליאור as one team: the weekly content plan (2026-11-15)

Code: `core/content_team.py`, `core/content_team_actions.py`, `agents/media_agent.py`
(`plan_week`, `_checkin_for_client`). Tests: `tests/test_content_team.py`. Never run against a
real model, a real client or a real network.

## The flow

1. **מאיה plans.** One planning call writes 2-3 items for the week. Each item has a day, a
   channel, a format and a ready caption. The call gets the approved brand kit, the names of
   files already in the client's media library and which channels are connected.
2. **One card.** The client approves the WHOLE plan in one card (`approve_content_plan`). The
   card lists every item with its day, channel and how it will be published.
3. **ליאור produces.** The approval starts production of every item in the background, through
   the existing `media_agent` functions (image, video, filming kit). One failed item does not
   stop the others; the team gets one alert naming what did not come out.
4. **מאיה holds the schedule.** Each item keeps its day and caption. The Media tab shows the
   plan, each item's status, "today", and a copy-caption button.

Saturday night: the weekly run opens this one card (and one short line in the chat). If the
card cannot be opened it falls back to the older per-item suggestions, so the run never fails.

From the chat: either agent proposes `build_content_plan`. The plan is written and the client
gets the approval card straight away. A plan that is already waiting is shown again, not
rewritten, unless the client asked for a change.

## Publishing, as it really is today

`content_team.AUTO_PUBLISH` is False for every channel: Meta's verification is not finished
and TikTok has not approved the app. So:

- connected channel → "we prepare and schedule it, you publish in one tap";
- channel not connected → "the file and the caption wait in your media library";
- website image → goes into the media library.

Every item of the plan shows its line, and the card and the Media tab carry one sentence
explaining why. **There is no auto-publish code in this module on purpose.** Flipping the
constant must not be enough to post on a client's page. When a channel is approved, the
publish path gets built, tested and then switched on.

What "one tap" means today: the file and caption are ready; the client posts it. For a TikTok
or YouTube video the agents can still propose the existing cards (`send_tiktok_to_inbox`,
`publish_youtube_from_media`) where those are available. There is no reminder on the day
other than what the agents and the Media tab show ("today").

## One team in the chat

`support_agent.STUDIO_RULE`, section ONE TEAM, for every agent:

- never send the client to another agent or chat, never "I can't" for something the team does;
- the plan is one card, never item-by-item approvals;
- publishing is described from `content_plan.publishing_by_channel`, never as automatic;
- inspiration is research only: never copy a competitor's ad, creative, slogan, character,
  music or trademarked material.

Every agent's data carries `content_plan` (the plan, each item's state, the channel modes) and
`branding` (the shared kit).

## Storage

`app_settings` `content_plan:<id>` (the current plan only). Cleared with the client. No
migration.

## Not built

- Auto-publishing on any channel (see above).
- A push reminder on an item's day.
- Editing one item inside a waiting plan: a change request writes a new plan.
- A decline of the card does not mark the plan declined; it simply expires after 5 days or is
  replaced by a new one.

## A specific request is not the plan (2026-11-22)

Client 27 asked ליאור for "a post of 3 reasons why custom design is a special
gift — 3 posts, one reason each" and got the weekly content plan, plus "I
can't make and show you the images separately from the plan". That was
WIRING, not a limitation: `media_agent.generate_image` / `generate_video` run
on their own (the client's own creative tool, saved to Drive `images/<platform>`
/ `videos/<platform>`, nothing published); only the plan's approval ever called
them. Now:

- `produce_requested_content` (core/agent_actions): ONE card with exactly the
  pieces asked (1-6, image or short video, each with its brief), slow + STOP_FINISH,
  files to Drive for review, published nowhere. Tool not connected → the card
  switches to `connect_creative_tool` and says that is what unblocks it. Media not
  in the package → a plain line + what can be done now (the texts).
- `build_content_plan` is proposed ONLY on an explicit plan / week / calendar ask.
- `core/reply_guards`: a specialist's reply that pitches the plan for a message
  that did not ask for one loses those sentences, and the plan card is dropped.
  The manager still orchestrates.
