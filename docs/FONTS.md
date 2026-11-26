# Brand fonts (2026-11-26)

Before this, a client's typeface reached the agents as words only and every
post was drawn in Rubik and Assistant. Now the names in the brand brief are
real font files, the slide renderer draws with them, and every statement
about the font of a post comes from what the renderer drew.

| Module | What it owns |
|---|---|
| `core/font_library.py` | the fonts the renderer can draw with: the two bundled families, the pinned Hebrew library, a client's upload — one shape (`Face`) |
| `scripts/fetch_hebrew_fonts.py` | `--install` / `--check` (the build), `--refresh` (a developer) |
| `assets/fonts/hebrew-fonts.lock.json` | the PINNED set: family, category, weights, coverage, and per file its URL + SHA-256 |
| `core/brand_fonts.py` | the client's choice resolved to faces (`resolve`), their own uploaded fonts, the page's pictures |
| `core/slide_renderer.py` | which font draws which RUN (`_Type`), `render_sample` |
| `core/render_manifest.py` | the font that drew each block, and every fallback, as card lines |
| `core/copy_budget.py` | budgets measured with the client's font, dropped when it changes |

Tests: `tests/test_brand_fonts.py`.

## 1. The Hebrew library

Every Google Fonts family that carries the Hebrew subset (open source, OFL /
Apache): 62 families, 187 static files, about 90 MB. Upright weights only —
the renderer has no italic and Hebrew has none.

**The files are not in git and not in the build context.** `assets/fonts/library/`
is in `.gitignore` and `.dockerignore`. What is in git is the lock. The image
gets its fonts in the Dockerfile, in two steps:

1. `fetch_hebrew_fonts.py --install` (before `COPY . .`, so the layer is cached
   until the lock or the script changes): downloads every pinned file, compares
   its SHA-256, writes only a file that matches. A file that could not be
   fetched gets one more pass. Anything still missing or different → exit 1.
2. `fetch_hebrew_fonts.py --check` (after `COPY . .`, on the image as it
   ships): reads the library back from disk — every pinned file with its hash,
   no font file the lock does not pin, and a lock that pins something (an
   empty or cut-off lock is a failure, never "nothing missing"). Exit 1 = the
   build fails, the same way the background-removal model is verified.

So an image without its fonts cannot be built. If one ever runs anyway,
`slide_renderer.startup_check` logs `FONT LIBRARY INCOMPLETE` and raises an
admin alert, the branding page marks the family "not available now", and a
post whose brand font is missing says so on its card (below) — a fallback to
Rubik is never silent.

A build that fails on this step with "could not fetch" is a network drop
between Cloud Build and fonts.gstatic.com: run the deploy again. A build that
fails with "SHA-256 … is not the pinned …" means Google replaced a file:
refresh the lock (below), review the diff, commit.

**Refreshing the lock** (a developer, needs `fonttools`):
`python scripts/fetch_hebrew_fonts.py --refresh` asks Google Fonts for the
current Hebrew families, downloads each weight, verifies the 22 letters and
five final forms are really in the file (a family listed under "hebrew" whose
file lacks letters is skipped and recorded in `skipped`), and rewrites the
lock. Never edit the lock by hand.

**Weights are real files.** A family without the asked weight draws with its
nearest real one (`Face.weight_for`). Bold is never synthesized — there is no
stroke or thickening anywhere in the renderer (a test scans for it). A family
with no bold says so on the page and names the nearest family (same category,
closest measured Hebrew width) that has one.

## 2. A client's own font

Branding page → "גופן משלכם". TTF, OTF, WOFF, WOFF2, up to 15 MB, up to 8
families per client. `brand_fonts.add_upload`:

- inspected with fonttools and reported in plain words at once: the whole
  Hebrew alphabet or not (and which letters are missing), Latin or not, the
  weights and what is missing (no bold / no regular / one weight);
- WOFF / WOFF2 are converted to a plain TTF / OTF; a variable font is
  instanced to one real static file per weight (if instancing fails, its
  default weight is kept and the report says so);
- a second file of the same family adds its weight (Regular, then Bold);
- **a font without Hebrew is kept for Latin text only** and is never saved as
  the brand font without a Hebrew pairing (`needs_pairing`);
- the rights confirmation is a dated LOG — not legal advice and not a gate
  (knowledge/client-governance.md §7). An unticked upload is still stored;
- only what cannot be a font at all is refused (`bad_font`, `font_unreadable`).

Storage: the static files live in the client's own Drive `brand` folder
(`appProperties.uallak_kind = brand_font`, so the branding page's file list
does not show them). The record — weights, file ids, SHA-256, coverage, the
rights log — is on the brand brief's row (`brand_brief.page_state` → `fonts`).
At render time a file is fetched once into the instance's temp folder, checked
against its recorded SHA-256, and reused; a stored file that is not the one
that was checked is refused.

## 3. The renderer

`brand_fonts.resolve(client_id)` → per slot (`headline` = headline + kicker,
`body` = everything else) a chain of faces: the brand font, then the client's
Hebrew pairing. The renderer adds the spec's own family and Rubik as the last
resort. **Every `render_slide` / `render_set` call outside the renderer passes
`fonts=`** (a test fails on one that does not), so the card, the preview and
the file are drawn with the same fonts.

Fallback is per RUN, never per glyph: a line is split into direction runs, a
word is drawn whole by the first face of the chain that has all its
characters, and neighbouring words of one face are one run. A Latin brand font
with a Hebrew pairing draws "המותג Nike Air כאן" as Hebrew / Latin / Hebrew.

**The manifest says what drew.** Each text layer carries `font`, `weight` (the
real one), `weight_asked` and `fonts` (one row per font and script). The
manifest's `fonts` = what the brief asked for and every run another font drew,
with the reason: `unknown_family`, `no_hebrew_pairing`, `missing_glyphs`,
`unavailable`. From there, never from the brief: the line on the design card
and the Social item (`font_lines`), `drawn.fonts` / `drawn.font_fallbacks` for
the agents (`FACTS_RULE`), and the `[font_trace]` log line per block.

**The copy budget moves with the font.** `copy_budget.budget` and `fit` measure
with the resolved fonts and key their caches on `resolve()["signature"]`;
saving typefaces, uploading or deleting a font calls `invalidate`. A font
change that does not move the budget is a bug (tested: Rubik → Assistant
raises the headline's `max_chars`).

## 4. The branding page

`brand_page.save_typefaces(headline, body, hebrew)` writes
`visual_identity.typefaces: headline: X; body: Y; hebrew: Z` (the `hebrew`
part only when chosen). A name we have no file for is saved as the client
states it — the page and every post then say it is not available.

- the picker: the client's own fonts, then the library grouped by category;
  each row is a picture of the font drawn by the renderer
  (`GET /api/client/brand-identity/font-sample`);
- the preview under each colour variant is ONE real slide — the client's
  template, palette variant and fonts, saved or not yet
  (`GET /api/client/brand-identity/preview`; the fonts that drew it travel in
  `X-Render-Fonts`; a brand that cannot be drawn answers 422 with the reason a
  post would fail on). Nothing on the page is CSS imitation;
- notes: no bold (+ switch to the nearest family that has one), a font without
  Hebrew (the pairing slot opens at once), an unknown name, files not installed.

Routes: `POST …/font/upload` (raw body, `X-File-Name`, `?rights=1`),
`POST …/font/{id}/rights`, `POST …/font/{id}/delete`, the two GETs above.
Staff view is read-only (the GETs are in `admin_view.READ_ENDPOINTS`).

## Not done

- Setting typefaces from the chat is still a P3 gap (`core/chat_parity`).
- The renderer tests need Pillow with raqm: they run in CI, and are skipped on
  a machine without FriBidi.
- Nothing here has run against a live client's Drive.
