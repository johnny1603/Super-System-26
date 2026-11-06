# uallak landing design playbook

Distilled in our own words from established design-system and interaction-design practice, adapted to Hebrew RTL brand pages. The landing agent reads this together with `shin-sekai-gold.html` (the gold-standard reference page). Adapt, never copy: every client gets a page that looks like THEIR brand.

## 1. Process (always in this order)
1. Strategy: who is the visitor, what do they want, what is the one action, what proof do we really have.
2. Design brief: mood (3 adjectives), palette from the logo and photos, font pairing, signature moment, layout rhythm.
3. Build.
4. Critique against the checklist at the end, fix, only then show the client.

## 2. Hierarchy and layout
- One idea per screen. The hero carries one promise, one primary action.
- Desktop headline at most three lines; fluid sizes with clamp().
- Rhythm: alternate dark and light sections, wide and narrow, text-heavy and image-heavy. A single paper/bone section inside a dark page (or the reverse) creates a beat.
- Asymmetry, overlap, rotated stamps/cards, oversized outline glyph or logotype as watermark: use them with restraint, one or two per page.
- Generous whitespace: sections 56-104px vertical. Group by proximity before adding borders.
- 60/30/10: neutrals, secondary, one accent.

## 3. Typography
- One expressive display face for headlines plus one calm text face. Hebrew-capable Google fonts by mood: Secular One (bold, characterful), Rubik (friendly modern), Heebo/Assistant (neutral workhorse), Frank Ruhl Libre / David Libre (editorial, luxury), Suez One (confident serif), Karantina (condensed street), Varela Round (soft), Alef (clean).
- A secondary script tied to the brand (for example Japanese Shippori Mincho for a Japanese-named brand) used only as accent.
- Hebrew body line-height 1.6+, body 17-19px mobile-first, never light weights at small sizes.

## 4. Color
- Derive from the client's logo and photos. Never default to generic blue/grey gradients.
- Warm ink and bone instead of pure black and white.
- Contrast: body 4.5:1, large text 3:1. On a bright accent button use dark ink text.
- One accent color, plus at most one small secondary highlight.

## 5. Backgrounds and texture
- Layered soft gradients that drift slowly, film grain at 5-8% opacity, patterns, image with overlay. Never a flat default fill.

## 6. Iconography
- Custom inline SVG in one stroke language (1.8px, round caps and joins) with one accent-colored detail. No emojis, no icon fonts, no stock icon packs look.

## 7. Motion
- Purposeful: entrance, orientation, feedback, one signature moment (the peak).
- Loader/intro with the logo: under 2 seconds, always with a fail-safe so it can never block the page.
- Scroll reveals 0.6-0.9s ease-out, stagger 60-90ms. Hover 150-250ms. Press scale .98.
- Animate transform and opacity only. Never block input. Honor prefers-reduced-motion by disabling non-essential motion.

## 8. States and microinteractions
- Every control has default, hover, focus-visible, active, disabled, error and success states.
- Forms: one question at a time, auto-advance after a choice, progress indicator, inline friendly Hebrew validation that never blames the user, correct input types and autocomplete, phone field direction LTR.
- Success state is the peak-end moment: a short celebration plus the next step (continue in WhatsApp).

## 9. Conversion path
- Primary action repeated at hero, mid-page, end, and as a floating/sticky button on mobile.
- WhatsApp link carries a prefilled message with context (the page, what the visitor chose). Click-to-call on mobile.
- Remove excise: no unnecessary fields, no second confirmation, no hidden next step.
- Proof only from real facts supplied by the client. Never invent prices, reviews, numbers, guarantees, scarcity.

## 10. Hebrew RTL
- Logical CSS properties (inline-start/end), mirror directional icons and progress, wrap Latin/numbers in dir=ltr where needed, check mobile first, touch targets at least 44px.

## 11. CTA and microcopy
- Specific verb phrases ("דברו איתנו בוואטסאפ", "קבלו הצעה מותאמת"), warm direct second person, no generic "שלח".

## 12. Performance and accessibility
- Lazy-load below-the-fold images, alt text on every image, proper heading order, visible focus ring, no layout shift, preload the key font, keep the page light.

## 13. Anti-patterns
Emojis as icons; default blue; stock gradient hero; everything centered; walls of text; carousels hiding key info; fake urgency; invented proof; more than one accent; motion that delays the content; a page that looks the same for every business.

## 14. Critique checklist before the client sees it
- Is the single promise clear in 3 seconds on mobile?
- Does the palette, type and imagery feel like this brand and this niche?
- Is there one memorable signature moment?
- Do all buttons work and go to the right place?
- Contrast, focus, reduced motion, tap targets OK?
- Any claim we cannot prove? Remove it.
- Would a competitor's page look identical to ours? If yes, push further.
