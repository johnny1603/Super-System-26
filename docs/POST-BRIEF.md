# The post contract: social agent ↔ media agent (2026-11-23)

**What it fixes.** A carousel request reached the image model as one free-text brief, with copy and visuals mixed together. The result was four random AI sneakers and zero readable text. The fix is a contract, not a better prompt.

**Code:**
- `core/post_brief.py`: the contract and the rules (pure logic).
- `core/post_pipeline.py`: request → copy in the Social tab → production.
- `agents/media_agent.generate_from_brief`: one asset.
- `core/social_queue.py`: the "post" item.

**Knowledge, loaded in full:**
- `knowledge/social-craft.md`
- `knowledge/brands/<client_id>.md`
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
   - `real_photo`: flagged as needing a real photo. It is never generated.
   - `generated_*`: `generate_from_brief`. The prompt comes from `visual_direction`, must_include / must_avoid and the palette only. `assert_copy_free` fails the call before the model if the prompt carries any of the copy (the whole text, or 4 of its words in a row) or any Hebrew.
   - Each file joins the queue as its own item (`post_ref`, `flags`) for the publish approval, as before.

## Not built yet

- Picking a real photo from the client's library automatically.
