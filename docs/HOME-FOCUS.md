# Home focus: agent invitations, prerequisites, first tasks, goals

Module: `core/home_focus.py`. Routes: `GET /api/client/home-focus`, `POST /api/client/invite`.
Tests: `tests/test_home_focus.py`.

## 1. Agent invitation cards (2026-11-16)

These cards look like Lior's branding banner: the agent's face, one persuasive line, and a
chevron. They appear on home, right under the branding banner.

**The rules:**
- **Only a real service.** Each card is a service the system has for this client.
- **Only while not started.** A card shows only while the client has not started that service.
- **At most three** (`MAX_INVITES`).
- **Fail closed.** A package that cannot be read shows no cards.
- **No tool or vendor names**, and no offer of the card's own.

| Card | Agent | Shown when | Hidden once |
|---|---|---|---|
| `avatar`: your own AI character | Lior | the package has the avatar step AND the presenter gate is open (`avatar_video.presenter_allowed`) | a character exists (`avatar_agent.list_ready_avatars`) |
| `social`: "ready to take your social presence a step further?" | Maya | the package has the Meta step | a weekly plan was approved (`content_team.latest`) |
| `organic`: be found on Google without paying per click | Uri | always: a package without organic gets the taste and the upgrade card in the chat | a report exists (package with organic) |
| `google_ads`: customers searching for you right now | Yoav | the package has the Google Ads step | a campaign-created activity row exists |
| `automation`: never miss a customer | the client's manager | always (a manual service, built with the team) | an active automation account row |

**What a click does:**
- It sends the client's own opening line (`INVITE_TEXT[...]["opener"]`) to that agent.
- The agent answers with the next step as a ready proposal, by its existing rules:
  - `build_content_plan` (Maya);
  - `build_seo_report` or `upgrade_to_organic` (Uri);
  - `create_presenter_from_photos` (Lior);
  - the team path for automations (`_AUTOMATION_RULE`).
- The card itself decides nothing.

**Ownership:** organic SEO is Uri's in the code (the website persona owns the SEO report).
Lior keeps branding and the AI character.

**Yoav:** there is no client card that launches a campaign. Campaigns are created paused and
reviewed by a person. So Yoav's line says "we'll plan a first campaign together; nothing runs
before you approve it".

## 2. Prerequisites: one check for every card and the chat

`SERVICES[...]["needs"]` lists what each job needs connected:

| Service | Needs |
|---|---|
| media / avatar | the AI image and video tool |
| social | Facebook and Instagram |
| organic | the website (plus the site's Google search data when that round-2 flag is on) |
| google_ads | the Google Ads account |
| branding, automation | nothing |

`prerequisites()` returns what is still missing.

**A click with something missing:**
1. The agent's thread gets one calm message in its own chat: "Before we can start, let's finish
   connecting what I need for this:". It lists exactly the missing items, each with why this job
   needs it, and says it will come back to the request afterwards.
2. One button per item opens the existing connect conversation
   (`connectViaChat` → `connection_catalog.flow`). That conversation gives the sign-up link from
   the catalog when the client has no account yet.
3. The request is kept in app_settings `invite_resume:<id>`. It is forgotten after 14 days.
4. When everything is connected, the next home load gets it back once (`resume`). The page then
   sends the original request to that agent.

**In the chat:** the same check reaches every agent as `prerequisites` in the payload
(`prompt_block`, cached 60 s), with each item's connect card. Its rule is
`support_agent.PREREQ_RULE`.
