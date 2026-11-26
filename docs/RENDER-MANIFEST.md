# The render manifest — what was drawn, not what was planned (2026-11-26)

`knowledge/client-governance.md` §8: *an agent reports what was produced, never what was planned.*
`core/render_manifest.py` is how the code holds that.

## What happened on client 27

A card said "כולל דמות שנוצרה ב-AI" over slides that showed no character.

- The notice was decided from the BRIEF: `slide_design.character_flags` raised it whenever a slide had a pose
  selected, and `post_pipeline.submit` stored it on the post card the moment the brief named a character.
- The picture the client looks at on a design card is drawn by `slide_design.preview_png`. That call never
  passed the slide's pose to the renderer (`character=` was missing), so the preview was always text only.
  The final files (`_finalize` → `render_set`) did pass it.
- Reproduced locally before the fix (stubs, client 27's palette): pose selected, flag `ai_label` on the card,
  and the preview's manifest = `['background', 'text', 'counter']` — the stage log line
  `path=preview … stage=composited outcome=NOT DRAWN`. Not verified on production logs (no access).

## The manifest

`slide_renderer.render_slide(..., manifest={})` fills the dict with every layer it committed: background,
accent, character (id, pose, box), logo, each text field with its final string, the counter.
`render_set(..., manifests=[])` gives one per slide. A generated asset gets `render_manifest.for_asset(out)`
(what the generator reports it was given — not a reading of the pixels).

Stored: `design.manifests` on a design item, `manifest` on every finished file item, `drawn` on each asset of a
post's production record.

## What is built from it

| Statement | Source |
|---|---|
| The AI notice (`ai_label` a character, `ai_content` a generated picture) | `render_manifest.disclosure(manifest)` — iff a generated element is in it. `social_queue.true_flags` rebuilds it on every read; a stored `ai_label` from a brief is ignored |
| The post card, per slide | before a render: `character_planned` (a plan). After: the manifest's notice, or `character_not_drawn` |
| A finished slide file's notices | its OWN manifest (`_finalize`), never the set's |
| What the agents may say | `social_queue.prompt_block`: `drawn` / `not_drawn` per item + `facts_rule`; `CONTENT_CONTEXT_RULE` "DRAWN, NEVER PLANNED" |
| The production line in chat | `post_pipeline.announce`: with an asset called made and no manifest behind it, the line says it could not verify — it does not describe the post |

## Declared and not drawn = FAILED

`render_manifest.declared(slide)` (the brief: characters, headline, body) against the manifest
(`render_manifest.missing`). When something is missing:

- the design item: `state: "failed"`, `missing: [{type, id, slide, reason, line}]`; no approve button;
  `social_queue.approve` refuses it (`element_missing`); `_finalize` refuses to write files;
- the post's production: `state: "failed"` (never "partial"), `missing`, the chat line `TEXT["missing"]`;
- a generated asset whose declared character is not in the result is not queued at all;
- ways out, both the client's: approve a pose (the design re-checks itself — `apply_approved_poses`), or
  "להמשיך בלי הדמות" (`slide_design.without_character`: the DECLARATION changes, recorded in `design.dropped`).

## The palette (2026-11-27)

Client 27, 2026-11-26: a carousel's card read `צבעים: black + red` over a dark
teal first slide. The card's colour label was `design.palette.label` — the
variant the BRIEF named (the template name "carousel black + red" is built from
the same label). Nothing compared it with the file. The renderer does take its
roles from the brand palette, but a slide can legitimately be drawn in other
colours (the manager's explicit colours, post `colors` → `slide_roles`), and the
card kept saying the brand variant either way.

- `manifest["palette"]` = `{"roles": the four colours the slide was drawn with,
  "background": {"kind", "role", "mean": the canvas's average colour measured
  from the pixels after the background layer}, "variant": the brand variant
  those colours ARE, "" when they are not}` (`render_manifest.palette`,
  `name_palette`).
- Declared for a slide (`slide_design.declared_for`) = the brief's variant roles
  with that slide's explicit colours on top. `render_manifest.missing` compares:
  other roles, or a solid background whose pixels are not the declared colour =
  `{"type": "palette"}` → the design card FAILS by name, cannot be approved,
  `_finalize` / `render_asset` write no file (`palette_not_used`).
- The card's colour label, chips and note come from `render_manifest.palette_view`
  over the manifests: the variant's name only when its colours were drawn;
  otherwise "not the brand palette" with the drawn hexes (and the manager's own
  request beside it). Before a render (the copy stage) the label says it is the
  brief's and unchecked. A file made before this date carries no palette: nothing
  is claimed about its colours.
- Agents: `drawn.palette.brand_variant` ("" = not the brand palette), FACTS_RULE.

**Palette discipline (same day).** A slide's colours are the DEFINED values of the
brand's palette variants (`brand_brief.palette_colors` + the variant's resolved
roles). `manifest.palette.outside` = the drawn role colours the brand does not
define.

- Outside colours WITH a reason in the brief (`post.colors.requested` = the
  client's own words, or `post.colors.reason` = the agent's; carried per slide
  as `colors_reason`) = a NAMED DEVIATION on the design card: the slides, the
  colours, the reason, whose it is. The card is blocked until the client decides
  (`slide_design.decide_deviation`, `POST …/design/deviation?decision=accept|brand`):
  `accept` records who and when (keyed to those exact colours); `brand` drops the
  slides' own colours and draws the set again in the brand palette. Approving
  while undecided = `deviation_undecided`.
- Outside colours with NO reason = `missing` (`deviation_unexplained`): the card
  FAILS like a missing character — no approve, no files; the one way out is
  `brand`. `post_brief.clean_colors` also refuses `colors` with no reason.
- A colour of ANOTHER variant of the same brand is inside the palette: no
  deviation (the label still does not call it the declared variant).
- Agent rule: PALETTE DISCIPLINE in `support_agent` (creativity inside the palette).

## The stage log

`[character_trace] client=… post=… path=… slide=… character=… stage=… outcome=…` — one line per stage:
`brief_declared → pose_selected → pose_fetched → background_removed → composited → file_written`.
The last 60 rows of a design's own run are kept on the item (`design.trace`); `render_manifest.lost_at(rows)`
names the first stage that did not end "ok".

## Limits

- An LLM's free text cannot be mechanically prevented from describing a plan as a fact; it is given only
  manifest facts and the rule. Code-written lines are gated.
- `for_asset` trusts the generator's own report of which characters it placed; nothing reads the pixels.
- `tests/test_render_manifest.py`.
