# Client ownership & portability

Built 2026-10-30. Principle: every account, key, asset and record belongs to the CLIENT'S
business; uallak holds access only, and leaving is easy and dignified — a selling point.
Code: `core/ownership.py`, the routes in `core/api_server.py` ("Ownership & portability"),
the dashboard view `viewOwnership`, the admin drawer button "ערכת יציאה".
Tests: `tests/test_ownership.py`. No Google OAuth scope changed.

## "החשבונות והנכסים שלי" (dashboard view `ownership`)

- `GET /api/client/ownership?lang=` → one entry per connected disconnect group: capability
  title, the account's identity (Ads customer id, Page / channel link, site URL, CRM / automation
  vendor), what access uallak has, connected since, status (connected / pending / needs_attention),
  and one line on where the key lives. **No key, token, last-4 or `has_secret` — ever.**
- Per-account "הסרת הגישה שלנו" = the existing `POST /api/client/disconnect` (two-step arm, no
  dialog). Groups now include `crm`, `manychat`, `make`.
- WordPress disconnect now also deletes **our** Application Password on the client's site
  (introspect → delete that uuid; nothing else on the site). Unconfirmed (site down / WP < 5.7)
  is reported as `revoked: False`. **Not tested against a live site.**
- The creative-tool cards no longer show the key's last 4 characters (`vendor_tools.statuses`).
- Assets: website, hosting (built by us), landing pages, landing domain, Drive media folder, leads.

## "ייצוא הכל"

`GET /api/client/export-all` → one ZIP for the session's client: leads, media & videos (links —
videos never pass through our server), outreach, landing pages, activity report, billing (.xlsx),
the handover document (.docx) and the full record (.json, the same builder as the existing data
export). A failed part is listed in README.txt instead of failing the download.

## Handover document + offboarding kit

- `GET /api/client/ownership/handover?format=pdf|docx` — the client can download it any time.
  Hebrew only. Per account/asset: identity, our access, transfer steps (the vendor is named there,
  because that is where the client acts).
- Admin: drawer → "ערכת יציאה" → typed client id → `POST /api/admin/clients/{id}/offboarding-kit`:
  handover snapshot FIRST (opened for print, plus a Google Doc in the client's Drive `exports`
  folder shared with them when Drive media is configured), then `ownership.revoke_all` through
  `_disconnect_platform` for every group, an activity row, a short chat note to the client, and an
  admin alert for anything failed or not confirmed at the provider.
- Never deletes the client's data at a vendor; does not touch billing, status or records
  (closure/transfer and deletion stay separate actions). TikTok and the key-based tools: we delete
  our copy; the checklist tells the client how to revoke on their side.

## Ownership clause

Sales chat proposal terms (`proposal_term_ownership`), the report email (`term_ownership`) and the
terms page section 7 (`s7_p3`), 5 languages. The terms clause refers to section 9 for content,
which keeps the existing "provided it has been paid for in full" condition — a legal call.

## Secret guard (tests/test_ownership.py)

An AST taint check over every route in `core/api_server.py`: a value from `get_client_secret` /
`hydrate` / `use_vendor_key` / `decrypt` (or a helper returning one) may be passed INTO a call, never
returned or put in a Response. Plus an allowlist of the modules that decrypt at all, and the client
pages never naming a secret field.
