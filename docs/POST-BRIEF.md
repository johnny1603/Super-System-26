# The post contract: social agent ↔ media agent (2026-11-23)

**What it fixes.** A carousel request reached the image model as one free-text brief, with copy and visuals mixed together. The result was four random AI sneakers and zero readable text. The fix is a contract, not a better prompt.

**Code:**
- `core/post_brief.py`: the contract and the rules (pure logic).
- `core/post_pipeline.py`: request → copy in the Social tab → production.
- `agents/media_agent.generate_from_brief`: one asset.
- `core/social_queue.py`: the "post" item.

**Knowledge, loaded in full:**
- `knowledge/social-craft.md`
- the client's brand brief, from the database (core/brand_brief, docs/BRAND-BRIEF.md; a missing brief degrades, it no longer refuses)
- schema: `knowledge/brand-brief.schema.md`

## Flow

1. **Context.** Both agents load both files in full.
   - The social agent: the media and meta personas. The craft library goes in the system prompt, the brand brief in the data.
   - The media agent: every generation prompt, old path and new.
   - If either file is missing, `ContextMissing` is raised. The request gets a clear line and an alert, and nothing is produced.
2. **`produce_post`** is proposed by the social agent. It carries:
   - `post_format`, from POST_FORMATS, with a one-line `format_reason`;
   - `objective` and `audience`;
   - `caption` and every slide's copy;
   - one asset brief per slide (craft §2.2).

   Format resolution:
   - "A carousel about why X" resolves to `text_carousel`, whatever was chosen.
   - If no format was chosen and the request is ambiguous, the manager gets ONE short question.
3. **Review by the media agent** (`media_agent.review_brief` → `post_brief.sourcing`), in priority order: real asset > template > generated.
   - A generated image on a proof slide, or in product_carousel / before_after / customer_story, is an objection.
   - Other generated frames are flagged `concept` on the card.
4. **Objections** (craft §2.4), one line each:
   - Round 1: nothing is queued, and the social agent revises.
   - Round 2: escalated. The card shows both positions and the manager decides.
   - There is never a third round. Rounds are kept per set_id in app_settings `post_drafts:<client_id>`.
5. **Palette.** `palette_variants` in the brief, resolved from the audience: once per post, one variant per set_id, never mixed. An unresolved audience gets ONE question.
   - **The manager's explicit instruction wins (2026-11-25).** The rule stops random palettes; it never overrides the person. "Colourful, not the brand colours" / "a different colour per slide" → the post carries `colors: {requested: their words, slides: [{bg, fg?, accent?, muted?}]}` (one entry for all slides, or one per slide; `post_brief.clean_colors`). Each text slide renders with its own colours (`slide_renderer.slide_roles`: a missing fg is white or near-black, whichever reads; an accent that would vanish becomes the fg). The queued line says the post deviates from the brand palette on request. No `requested` = no override.
6. **Lead time.** The brief's `lead_time` and the post's `occasion_date` give an order-by date, counted in Israeli business days, Sunday to Thursday.
   - If the publish date is on or after it: a "late" warning.
   - If less than 7 days remain before it: a "tight" warning.
   - Both are warnings on the card, never a block.
7. **Copy before pixels.** The copy waits in the Social tab as a `kind: "post"` item.
   - It shows slide by slide as text, with format, reason, audience, palette, flags and warnings.
   - The owner edits it (`POST /api/client/social/{id}/copy`) or approves it.
   - Approving it starts production (`post_pipeline.start`). A post item is never publishable: the gate refuses it.
8. **Production**, per asset, within its `budget_credits`:
   - `text_slide`: DRAWN with real fonts (since 2026-11-23, docs/SLIDE-RENDERER.md). A post's text slides become ONE design item in the Social tab (one template, one palette variant); the manager approves or critiques it. It is never sent to an image model.
   - Before ANY asset is generated, the media agent searches the client's connected Drive folders (`drive_assets.find_assets`, docs/DRIVE-FOLDERS.md) and chooses a real file itself; it is referenced by its own Drive id (never copied — docs/DRIVE-FOLDERS.md §Referenced, not copied) and its card shows the source folder + role (no image tool needed).
   - `real_photo` with nothing real: flagged as needing a real photo, never generated. On a proof slide or in product_carousel / before_after / customer_story that is an objection to the social agent.
   - `generated_*`: `generate_from_brief`. The prompt comes from `visual_direction`, must_include / must_avoid and the palette only. `assert_copy_free` fails the call before the model if the prompt carries any of the copy (the whole text, or 4 of its words in a row) or any Hebrew.
   - Each file joins the queue as its own item (`post_ref`, `flags`) for the publish approval, as before.
9. **Said in the chat, honestly (2026-11-25).** Client 27's carousel came out as black slides without Maya while the agent said "waiting for your approval, nothing wrong". Now:
   - On the approval the producer posts "התחלתי להפיק…"; the run is the `rerun_post_production` action started by the system (`agent_actions.start_system_run`), so the chat shows its progress card; it ends with `post_pipeline.summary`.
   - "Done" only when nothing failed. Otherwise the line lists what failed and why, in plain words (`production.problems`: per slide, `post_pipeline.REASONS`; a character drawn without → `agent_characters.failure_text`), what did work, and the offer to run it again. A design drawn without the character asked for is `partial`, never `done`.
   - The agents' data (`social_queue.prompt_block` → `post_production` + its rule) lists every approved post whose production failed, partly failed, was stopped or is stuck; the persona must say so in the same reply and offer `rerun_post_production` (runs on the manager's yes).
10. **Speed: one pass, in parallel, in its own request (2026-11-25).** A four-slide carousel with characters took about five minutes, and the manager stopped card 69.
    - **Parallel.** `post_pipeline._generate_all` submits every non-text asset of the post together, `GEN_PARALLEL` (4) at a time, and waits for them as a set. The design step makes its new character poses together the same way (`agent_characters.poses_for`, `POSE_PARALLEL` 4). Library lookups still run in order, so the last pose is never repeated. Inside one post, the choice of a real file or a library pose takes turns (`media_agent.one_post_choices`), because a used file "ranks lower next time"; only the vendor wait runs in parallel. The pose library's writes are locked (one app_settings row).
    - **Budget.** Before a paid generation is submitted, what was spent plus the budgets of the generations still running must be under the post's budget. A first wave of 4 can still go over by what those 4 spend beyond their own budgets.
    - **Polling.** Vendor status checks come after 2, 2, 3, 3, 4, 5, 6 s, then every 8 s, for the same ~10 minutes in total (`media_gen_service.poll_waits`).
    - **Its own request.** The service has request-based CPU, so a thread started after a response only moves while some request is in flight (the card's poll backs off to 30 s; the scheduler tick fires once a minute). The run used to be one pass in code but moved only in those windows. Now `agent_actions.run_in_own_request` calls `POST /api/actions/{id}/drive` on the service itself (X-Admin-Key), and that request runs the WHOLE pass. One driver per row (`action_drive:<id>`). The per-minute tick (`drive_due`) only re-sends a call that never arrived (`action_drive_wait:<id>`, after 45 s). Without ADMIN_KEY, or when the call is refused at once, the pass runs on a thread here, as before.
    - **The bound.** One request lasts at most ~290 s: the proxy's read timeout, and Cloud Run's 300 s default, since the deploy sets no `--timeout`. A longer pass continues with throttled CPU; its 20-minute deadline and the reaper still end the row.
    - **Measured on every run.** `production.timing` = generate_s, design_s, total_s; the log line "pass ended in Ns".
    - **Stop (card 69).** `rerun_post_production` is `STOP_DISCARD` now (it was `STOP_FINISH`, so a stop let every slide be generated, paid and added). The card ends at once. Between steps the run checks the stop: nothing new is submitted, and no design or pose is made. A generation already at the vendor (paid, can't be cut) is kept as a waiting draft in the Social tab and recorded. Production ends `stopped`, never `running`, and its credits count only what was made.
11. **Approving starts production, and the chat shows it (2026-11-25).** Approved in the Social tab, production DID start, but the chat showed nothing until the window reloaded (the tab's approve never touched the chat; an open window never fetches new lines). The manager typed "אישרתי", which matched nothing and got "no card was made".
    - ONE path for every approval (the Social tab, the approval card, a typed yes): `post_pipeline.start(..., report=)` returns one entry per post: `{item_id, persona, title, state: started | already_running | failed_to_start, line, card}`. The social approve route, `approve_social_items` (→ the decide response) and the chat answer (`productions`) carry it.
    - The dashboard's `showProductionInChat` draws it in the producer's window AT ONCE: the start line and the running card. A window not open yet opens quietly (its history draws both); a minimised one comes back up.
    - Never two runs: a post whose `rerun_post_production` row is running and fresh (`post_pipeline.running_card`) is not started again. "אישרתי" / "approved it" (`chat_approval.FAST_APPROVE`), the model's "approve" with nothing pending, or a re-proposed production while one runs all answer `chat_approval.already_running`: "already running", where it is now, and the live card.
    - Never silence: production that cannot start (`start_failed`, e.g. its state can't be saved, or no card and the run itself fails) is said in the producer's chat at once with the reason (`REASONS["storage"]`, `["not_started"]`), the post is marked failed, and the team is alerted.
    - `tests/test_approval_starts_production.py`.
12. **A real progress bar (2026-11-25, `core/production_progress`).** The indeterminate stripe is gone for a post's production.
    - The real step list is computed when production starts (`plan_for`): a generation per image/video slide, a pose + a background removal per character slide, the layout, a render per text slide, the assembly.
    - Each step weighs its expected duration: per kind, the median of its last 20 measured durations (`production_step_seconds`, at least 3, else a default); steps that run together share the wall time. A run keeps the weights it started with.
    - The steps report where they really run: `_generate_all`, `agent_characters.make_pose` (`_step`), `slide_design.open_for_post` (layout, then each slide through `slide_renderer.render_set(on_slide=)`), the assembly.
    - Stored per client (`production_progress:<id>` = {item_id: record}, its own row). A running step counts by its time against its expectation, capped at 90%. Never backwards (the max shown so far), never 100% before it ended. The line names the step running now ("מייצר פוזה 2 מתוך 4", "מרנדר שקופית 3").
    - Elapsed always. The remaining time only when every kind in the plan was measured, and marked "(הערכה)". A failed step lists its reason next to the bar; a run that ends short freezes the bar where it stopped (`card.progress_final`).
    - The same bar on the chat card (`chat_approval.progress_extra`, polled every ≤ 5 s) and on the Social tab's production card (`social_queue._production_view`, refreshed every 4 s while it runs).
    - `tests/test_production_progress.py`.

## Not built yet

- Picking a real photo automatically: built 2026-11-23 for the client's CONNECTED Drive folders (docs/DRIVE-FOLDERS.md). Files in our own media folder are not searched this way yet.
