# Text slides: a renderer the agent designs for (2026-11-23)

**Why.** Image models can't write Hebrew. Text slides are DRAWN from real characters with a real font, using Pillow with raqm text shaping (FriBidi + HarfBuzz).
- It runs inside the service, in milliseconds.
- No browser, no extra service, no image model, no per-image cost.

**Principle.** The agent DESIGNS but never writes rendering code.
- It writes a declarative JSON spec, which is its creative surface.
- The renderer is the safe engine that draws the spec, and refuses it when it isn't legible.

## Files

| what | where |
|---|---|
| **The spec schema** (the agent's design space, machine-readable) | `knowledge/slide-spec.schema.json` |
| Validation, palette roles, copy binding | `core/slide_spec.py` |
| The renderer and its guards | `core/slide_renderer.py` |
| Named, versioned templates and the manager's critiques | `core/slide_templates.py` (app_settings `slide_templates:<id>`) |
| The design loop (Social tab) | `core/slide_design.py` |
| The agent that writes and revises specs | `agents/media_agent.design_spec` (`DESIGN_SPEC_SYSTEM`) |
| Fonts, bundled (OFL, from google/fonts) | `assets/fonts/Rubik[wght].ttf`, `assets/fonts/Assistant[wght].ttf` |
| Plain starting specs, one per palette variant | `knowledge/slide-specs/starter-b2c.json`, `starter-b2b.json` |
| Role colours per palette variant | `knowledge/brands/<id>.palette.json` (27: **provisional** hex values) |
| Tests and golden images | `tests/test_slide_renderer.py`, `tests/golden/slide_*.png` |

## The spec, in short

- **Canvas:** 1080×1350 by default; 1080×1080 and 1080×1920 also work.
- **`palette_variant`:** set by the system from the brief and the audience, never chosen by the agent.
- **Background:** solid, image, split or gradient.
  - An image background is the client's OWN photo, with an optional scrim.
- **Text blocks:** `text_blocks[]` with role, text, anchor, align, `x_pct`, `y_pct`, `max_width_pct`, `max_height_pct`, `font_family`, size, weight, `line_spacing` and `color_role`.
  - `x_pct` is measured from the RIGHT margin inward.
- **Other parts:** accent, logo, counter, margins, `guards.headline_max_words`.
- **`role_overrides`:** a different layout for, say, the hook slide.
- **Colours are roles** (`bg`/`fg`/`accent`/`muted`), never hex, so switching the palette changes nothing else.
- **Text can carry placeholders:** `{headline}`, `{body}`, `{n}`, `{total}`.

## Drawing rules

- Hebrew is drawn with `direction="rtl"` and `language="he"`.
- Every line is placed by its right edge; centred lines are placed by their right edge too.
- Lines are word-wrapped in logical order, measured with the actual font.
- Hebrew is never left-aligned.

## Guards

The render FAILS with a specific problem list; text is never clipped or shrunk.
- **overflow:** a word wider than its box, more lines than the box holds, or text outside the margins.
- **overlap:** text blocks, or the counter, on top of each other.
- **headline_words:** more words than the limit (default 10; configurable per spec, 2–14).
- **contrast:** the WCAG ratio of the text colour against what is actually under it fails.
  - "Under it" means the composed photo plus scrim, split, gradient and accents.
  - It is measured at the 5th-percentile pixel of the text area.
  - The minimum is 4.5, or 3.0 for large text (size ≥ 40, or ≥ 32 at weight ≥ 700).

## Startup

- `startup_check()` runs when `core/api_server` imports.
- Without raqm or the fonts: one loud log line and an admin alert, and every render raises `RendererUnavailable`.
- The Docker image installs `libfribidi0`. Pillow's wheel bundles raqm and HarfBuzz and loads FriBidi at runtime.

## The loop

1. **A post's copy is approved**, through `produce_post` and `post_pipeline.produce`.
   - Its `text_slide` assets become ONE design item in the Social tab (kind `design`).
   - The whole set uses ONE spec and ONE palette variant.
   - The spec is the client's latest template for that variant, or else the plain starter.
   - The agent is called only when a guard fails.
2. **Asking for a design directly in the chat:** for example, "design a new look for the gift carousels, bolder, more red".
   - The chat action `design_slides` takes sample copy, the audience and the direction.
   - The media agent designs, and the design appears in the Social tab.
3. **The card** shows:
   - each slide, drawn on request from the spec by `GET /api/client/social/{id}/slide/{n}`, with nothing stored;
   - the spec in words and as JSON;
   - the agent's one-line rationale and the earlier notes.
4. **Critique:** free text in Hebrew, in the card's box (`POST /api/client/social/{id}/design`), or in the chat (`design_slides` with `item_id` + `critique`).
   - The note is stored against the template.
   - The media agent rewrites the SPEC. Every candidate is rendered and checked, which is free.
   - It gets up to 3 attempts, and a guard failure is fed back to the next one.
   - The card shows revision N+1.
   - If no attempt passes, the previous design stays and the card says why.
   - No credits are spent at any point.
5. **Approve**, through the same approve button or batch as the rest of the tab, which then calls `post_pipeline.start` and `slide_design.approved`:
   - The design is saved as a named template, with version, `created_at` and spec.
   - For a post, every slide is rendered, saved to the client's Drive (`images/instagram`), and added to the Social tab as **approved**, because those exact pixels were just approved.
   - Publishing still goes through the gate.
6. **Later designs** read the client's earlier critiques: their taste.
   - `slide_templates.restore` brings back an earlier version as a new one, and no history is lost.

A design item is never publishable: the gate, `mark_published` and edit all refuse it, and reject answers "use a critique".

## Safety

- Hebrew never reaches an image model.
  - The text-slide path has no model call; the test scans every function on it.
  - The copy-leak guard on the generated-image path (`assert_copy_free`) is unchanged.
- Background photos and logos must be inside the client's own media folder: a fresh parents walk per file, images only, at most 15 MB.

## Not done / decisions

- **Palette hex values for client 27 are PROVISIONAL**: the brief has none yet (its open question 2). Replace them in `knowledge/brands/27.palette.json`.
- **Other clients:** each one needs `knowledge/brands/<id>.palette.json` as well as the brief. Until then, their text slides fail with "palette missing", which is never guessed.
- **Logo:** the spec supports one from the media folder, but no client has a logo file referenced yet.
