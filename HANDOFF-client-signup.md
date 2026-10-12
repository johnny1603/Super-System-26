# HANDOFF — Client signup, passwords, Google sign-in, billing-exempt accounts (2026-10-13)

## What existed before (investigated first)

- **Custom auth, not Supabase Auth.** No Supabase Auth users exist.
- Clients were NOT only created by hand: `/api/checkout` (the sales chat, before
  PayPal) creates the `clients` row with name/email/phone/address, status
  `pending_payment`; PayPal verification flips it to `active`. `/api/clients`
  (X-Admin-Key) could also create one, server-to-server only.
- Login was passwordless only: 6-digit emailed code (`login_codes`, 10 min,
  5 attempts), then a 30-day HMAC `session` cookie.
- "Which package did they buy" — which drives the connection checklist, the
  interview gate, the 90-day clock, website self-provisioning and the feature
  catalogue — was read ONLY from the PayPal `subscription_created` row. An
  account with no checkout therefore had an empty walkthrough and could not
  self-provision a site, which is why a plain `billing_exempt` flag alone would
  have produced a half-working account.

## What was built

| Piece | Where |
|---|---|
| Password hashing, lockout, set/change/reset, Google identity, exempt accounts | `core/client_auth.py` |
| Revocable sessions (`sv` in the token vs `clients.session_version`) + Google sign-in state | `core/session.py` |
| `/api/login/password`, `/api/signup/request-code`, `/api/signup/complete`, `/api/oauth/google-login/{start,callback}`, `/api/client/account`, `/api/client/password`, `/api/admin/clients/new`, `/api/admin/clients/{id}/password-reset` | `core/api_server.py` |
| Exempt grant read as the package | `client_journey._chosen_package` (and `website_agent._package_includes_hosting` now reuses it) |
| Revenue: exempt excluded from MRR, active count, trend, margins; shown as `internal_accounts` | `core/admin_service.get_overview` |
| Login page: password / Google / code / setup / forgot | `dashboard/login/index.html` |
| Profile: change password, or set a first one via emailed code | `dashboard/profile/index.html` |
| Admin: "+ לקוח חדש (ללא חיוב)" form, "פנימי" badge, reset button in the client drawer | `dashboard/admin/index.html` |
| Emails: password-setup code variant, welcome/reset invite (link only, never a code) | `core/email_service.py` |
| Schema | `migrations/2026-10-13-client-auth.sql` |

### Design decisions worth keeping

- **"Signup" is credential setup on the row checkout already created**, not a
  second account-creating door. After payment the chat's button goes to
  `/login/?setup=1`; the confirmation email links there too. An email matching
  no client gets the same generic answer as one that does.
- **Email verification = the emailed code.** A password can only be created
  through `set_password_with_code`, so every password-holder has proven their
  inbox; that sets `email_verified_at`. Code and Google logins set it too.
  Account `status` stays payment-driven, as before.
- **Google sign-in never creates or hijacks an account**: it matches a linked
  `google_sub`, else the client whose email equals Google's *verified* email,
  and refuses a client already linked to a different Google account. The CSRF
  state is bound to the browser by a nonce cookie.
- **Code login was kept**, as the always-works fallback and the reset channel.
- **The admin never sets or sees a password.** Reset clears it, ends all
  sessions, and emails a set-a-password link.
- **No dependency added** — stdlib `hashlib.scrypt`.

## Required before it works

1. Run `migrations/2026-10-13-client-auth.sql` in the Supabase SQL editor.
   Until then password/Google/New Client/reset fail closed (code login is fine).
2. Google Cloud Console → the existing OAuth client → add the authorized
   redirect URI `{PUBLIC_APP_URL}/api/oauth/google-login/callback`
   (i.e. `https://app.uallak.com/api/oauth/google-login/callback`, or the
   run.app URL if `PUBLIC_APP_URL` is set to that). Scopes are
   openid/email/profile — non-sensitive, no verification review needed.
3. Deploy.

## Known limits / not built

- No rate limit on `/api/signup/request-code` beyond what `/api/login/request-code`
  already had (none) — each call emails a code; attempts per code are capped.
- An exempt account's grant is fixed at creation; changing its services means
  logging a new `account_created_by_admin` row (newest wins) — there's no edit UI.
- The client dashboard's upgrade flow assumes a PayPal subscription; it isn't
  meaningful for an exempt account and wasn't changed.
- Not verified at runtime: this machine has no Python. Inline JS was parsed with
  Node; the Python was reviewed by reading only.
