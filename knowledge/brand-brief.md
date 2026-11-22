# Brand Brief — schema and intake procedure
Applies to: every client. One brief per client.
Read by: all specialist agents, before any client-facing work.
Last updated: 2026-11-23

---

## 0. What this is

The brand brief is the client's facts. The craft libraries are how we work;
the brief is who we are working for. An agent that produces content without
reading the brief is guessing.

It is **data, not prose**. Stored per client, versioned, editable by the
manager in the UI.

## 1. Who writes it

Drafted automatically at client onboarding by the intake agent, from:
- the intake answers the business owner gave when the file was opened
- the client's website
- the client's existing social accounts
- the client's existing assets

Then **approved once by the manager**. The business owner never fills in a
40-field form; that kills the product promise.

## 2. Confidence marking — mandatory

Every field carries one of:
- `stated` — the client said it
- `observed` — taken from their site, store or social account
- `inferred` — we concluded it; **must be confirmed before it drives a
  claim in published content**

An agent may use `inferred` fields for direction. It may never publish a
factual claim that rests on one.

## 3. Fields

```
identity
  business_name, category, sub_category
  founded, locations, languages
  website, social_handles

offering
  product_lines[]: name, description, price_range, lead_time
  not_offered[]            things we must never imply are available
  proof_points[]           volumes, years, credentials, named clients

positioning
  one_line                 what this business is, in one sentence
  differentiators[]        what is true here and not true of the category
  category_norms[]         what everyone in this category says (to avoid)

audience
  segments[]: name, priority, buying_trigger, main_objection
  highest_value_segment    the one content should serve first

voice
  tone_attributes[]
  use_words[], avoid_words[]
  formality, emoji_policy
  language                 and any term that must never be translated

visual_identity
  palette[], typefaces[], logo_ref
  photography_style
  mascot / recurring_characters
  production_constraints[]

assets
  real_asset_sources[]     where genuine product and work images live
  gaps[]                   what we do not have and would need to shoot

constraints
  forbidden_claims[]
  forbidden_topics[]
  compliance_notes[]
  ai_disclosure_required   true if the account uses a synthetic person

conversion
  what_counts[]            what a result actually is for this business
  channels[]
  not_measured[]           explicitly out of scope, with the reason

seasonality
  peaks[]: period, why, order_by_date if lead time matters
```

## 4. Rules

- `not_offered` and `forbidden_claims` are checked before every publish.
- `highest_value_segment` outranks the loudest segment. Fans are not always
  the buyers.
- `lead_time` plus `seasonality` produce an order-by date. Any gift or
  holiday content that ignores the order-by date is wrong, however good the
  copy is.
- `not_measured` exists so agents stop proposing measurement the client has
  already rejected.
- When the manager corrects a draft, the correction is written back into the
  brief. A correction that has to be made twice is a brief failure, not an
  agent failure.

## 5. Review cadence

Re-confirm `inferred` fields at the first approval. Re-read the whole brief
quarterly, and whenever the client adds a product line or changes pricing.
