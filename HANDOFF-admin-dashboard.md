# uallak — Handoff: Admin Dashboard

**Context:** `CLAUDE.md`, `HANDOFF-fable5.md`, `HANDOFF-google-agent.md`, `HANDOFF-google-agent-phase2.md` are in the repo root. Read them first.

A visual mockup is provided: `uallak-admin.html` (I'll place it at `dashboard/admin/index.html` in the repo). It shows the intended layout — treat it as a design reference for structure and visual language (dark theme, orange accent, matching `dashboard/client/index.html`'s style), **not as a source of real data**.

## Critical instruction — read this first

**Every number in the mockup is placeholder/demo data.** Nothing in the real build should show invented numbers. Where real data exists (e.g. an actual client in Supabase), show it. Where it doesn't exist yet (e.g. no cost-tracking data has ever been recorded), show a genuine zero or an honest empty state ("אין עדיין נתונים") — never a fake number that looks real. This matters more than making the page look "full."

## Authentication

This is more sensitive than the client dashboard (shows every client's data + internal costs) — it needs its own admin login, separate from `ADMIN_KEY` (which is for server-to-server API calls, not a browser session). Build a simple login (e.g. a single admin password via a new `ADMIN_PASSWORD` env var, or reuse the email+one-time-code pattern from `core/session.py` scoped to Johnny's email) that sets a signed session distinguishing `is_admin=true`, checked on all admin routes/pages. Your call on which pattern fits better with the existing code.

## Sections to build (from the mockup)

1. **Overview** — MRR (sum of active clients' `monthly_management_total`), setup fees collected this month, internal operating cost this month, net margin, cost breakdown by category, 3-month trend (clients + MRR), churn this month, simple forward projection, and a "low margin clients" list (below some threshold, e.g. 70%)
2. **Clients** — table of all clients (name, package, status, connected platforms, last activity, this month's internal cost/margin), with a detail drawer per client: account info, action buttons (open their Google Ads account, send WhatsApp message via `wa.me` link — my number is already in the mockup, message via the support chat channel, view full activity history), and their cost/media breakdown for the month
3. **Alerts** — full list/history of everything routed through `agent_alert()` (currently `master_agent.py` writes to a local `data/alert_history.json` file — per Fable 5's earlier audit, this is ephemeral and resets on every Cloud Run deploy; **this dashboard needs alerts to persist, so migrating alert storage to a Supabase table is likely a prerequisite** — your call on the cleanest way to do this)
4. **Weekly reports** — list of reports sent per client (this depends on the Google Ads agent Phase 2 weekly-report feature — if that's not finished yet, this section should just show a genuine empty state, not fail)
5. **Settings** — WhatsApp number for notifications, alert email, weekly report send time, margin/cost-per-lead alert thresholds — decide whether these belong in env vars or a small settings table in Supabase

## The real gap this exposes: cost tracking doesn't exist yet

There's currently no mechanism anywhere in the codebase that records the cost of an AI operation (Claude API calls, image/video generation, SEO tool usage) against a specific client. Showing real cost/margin numbers requires this to exist first. You have discretion on how deep to go here for v1 — a reasonable minimum might be a new Supabase table (e.g. `client_costs`: client_id, category, amount, created_at) that gets written to by the agents that already do costly operations (image/video generation calls, if any exist yet — check what's actually built) — flag clearly what you implement now vs. what still needs future agents to report into it.

## General notes

- Follow the house blueprint and existing patterns throughout
- Use your judgment on anything underspecified here — same spirit as the Phase 2 handoff
- Flag what you deferred and why, same reporting style as your previous handoffs

Please read `dashboard/client/index.html`, `core/api_server.py`, `agents/master_agent.py`, `agents/client_agent.py`, and the current Supabase table structure before starting.
