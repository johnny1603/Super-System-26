// Cloudflare Worker for clients' OWN landing-page domains (docs/LANDING_PAGES.md §1).
//
// Route: the custom hostnames (Cloudflare for SaaS, "*/*" on the uallak.com zone's
// fallback origin lp.uallak.com). A visitor on https://lp.theirbusiness.co.il/<path>
// is forwarded to https://app.uallak.com/lp-host/<path> with the hostname they used
// in x-uallak-lp-host. The app picks the client from that hostname (verified ones
// only) and serves the page; Tag Manager loads only on this path.
//
// No secrets here. ORIGIN is the only setting. Nothing is cached at the edge:
// pages are small and a takedown must be instant.

const ORIGIN = "https://app.uallak.com";
const ALLOWED = /^\/(?:_uallak-verify|privacy|terms|[a-z0-9-]{1,60}(?:\/go\/(?:call|whatsapp|store))?)\/?$/;

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }
    if (url.pathname === "/" || !ALLOWED.test(url.pathname)) {
      return new Response("Not found", { status: 404 });
    }
    const path = url.pathname.replace(/\/$/, "");
    const target = path === "/_uallak-verify"
      ? `${ORIGIN}/_uallak-verify`
      : `${ORIGIN}/lp-host${path}${url.search}`;
    const headers = new Headers(request.headers);
    headers.set("x-uallak-lp-host", url.hostname.toLowerCase());
    headers.delete("cookie");   // a client's domain never sends anything to our app's sessions
    const response = await fetch(target, { method: request.method, headers, redirect: "manual" });
    const out = new Response(response.body, response);
    out.headers.delete("set-cookie");
    return out;
  },
};
