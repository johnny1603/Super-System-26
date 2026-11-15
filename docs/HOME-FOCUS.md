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
(`prompt_block`, cached 60 s), with each item's connect card. Its rule is the PREREQUISITES part
of `support_agent.STUDIO_RULE`.

## 3. New-client focus: the first three tasks

`home_focus.onboarding()` reports whether the client has done all three:

1. **Every required connection** (`client_journey.connection_status`). The button continues the
   connection walkthrough.
2. **The deep characterisation with the manager** (`interview_agent.is_completed`). This task
   is the large card with the manager's face. Its button starts the interview, which opens only
   once the connections are complete.
3. **Agreed 3-month goals** (`quarter_goals.agreed`). The button asks the manager, who proposes
   the ONE `agree_quarter_goals` card.

**"Done" means** the connections plus the characterisation (the owner's definition). Once those
are done and the goals are not agreed yet, the panel shows the goals task alone and hides nothing.

**While not done** (`body.onboarding-focus`):
- The home page shows the task panel (`#homeFocus`) at the top.
- The idea feed is hidden: the "waiting for your approval" list with its seasonal and generic
  ideas. A small counter says how many ideas will open later.
- Approval cards in the chats are never hidden.
- The invitation cards stay, below the tasks. A click from a client with nothing connected gets
  the calm "connect first" line.

**Fail closed:** a state that cannot be read (`known: false`) shows the normal home, never a
focus mode built on a guess. In the staff view the task buttons are not drawn.

## 4-5. The 3-month goals and their path on home

Module: `core/quarter_goals.py`. Card: `agree_quarter_goals` (`core/quarter_goals_actions`).
Store: app_settings `quarter_goals:<id>`.

**The card:** after the characterisation the manager proposes ONE card with three tracks:
- **Social:** posts a week, run by Maya.
- **Organic:** the keywords, starting from the sales chat's `organic_target_phrases` and only
  phrases the client agreed to.
- **What must improve.**

The click stores the goals, and a new card replaces them. Once agreed, the organic work focuses
ONLY on those keywords: STUDIO_RULE, and the SEO report's default focus.

**The path on home** (`track()`, `#goalTrack`): one step per track, each with the goal, where it
stands now and the next step. The page shows the month (1-3) and one honesty line ("shared
goals, not a promise").

| Step | "Now" (measured) | "Now" (not measured) | Next step |
|---|---|---|---|
| Social | images and videos prepared in the last 7 days (activity rows) | — | from the week's plan: approve it / in production / ask Maya |
| Organic | how many agreed phrases have a position, only when a tracking source really returned one | positions not measured yet, plus articles written in 30 days | connect the site / approve the next article |
| Improve | enquiries in the last 30 days against the 30 before (`client_leads`) | — | answer every new enquiry quickly |

Every "now" carries a measured / not-measured-yet tag. Nothing is estimated silently and nothing
is promised.

## Only the three tasks, a few ideas at a time, and the new-client view (2026-11-16)

- **During onboarding** (connections plus the characterisation not done), `body.onboarding-focus`
  hides the idea list and its pace line, the branding banner, the invitation cards, the goal
  path, the stats, the timeline and the journey. What stays is the greeting, the three tasks and
  the connection cards that task 1 leads to. The weekly ideas are still written, but
  `engagement_agent` does not announce them in the chat until onboarding is done.
- **Client 27.** It is internal and fully set up: connections complete and an old first-login
  interview on record, so `onboarding().done` is True and the feed shows. That was correct. What
  was wrong is that the banner and the invitations stayed visible during the focus; now they hide.
  Use the new-client view to see day one.
- **A few at a time** (`core/home_feed`). `/api/client/suggestions` returns at most 3 by
  importance and timing, plus `waiting`.
  - The order: a sensitive day first, then a dated event by how near it is, then a promotion,
    then tweaks, then plans and content documents, then ideas, then homework. Ties go to the
    newest.
  - Deciding one opens the next on reload, because the order is recomputed every time.
  - One calm line (`feed_pace`, 5 languages) says how many are waiting.
- **"View as a brand-new client"** (`core/new_client_view`): the admin client list has a "כלקוח
  חדש" button. It opens the same read-only admin view with the cookie mode `new_client` (signed,
  bound to the admin session, 2 hours, audited with "mode new_client").
  - What it shows: the dashboard, checklist, journey, interview state, home-focus, suggestions,
    open cards and chat history all answer as day one. Nothing is connected, there is no
    characterisation, no goals, no ideas, no cards, and the chat is empty. The required steps
    are the client's real package.
  - What it touches: nothing is written. It skips `note_connections_complete`, and it does not
    consume a resume.
  - The banner says "כלקוח חדש".
