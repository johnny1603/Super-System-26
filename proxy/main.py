"""app.uallak.com reverse proxy — the me-west1 domain-mapping workaround.

Cloud Run's native `gcloud run domain-mappings` is not offered in me-west1
(Tel Aviv), where the real app (`super-system`) runs. This service is the
workaround: a tiny Cloud Run service deployed in a region that DOES support
domain mappings, whose only job is to forward every request byte-for-byte to
the me-west1 backend and stream the response back.

It holds no state, no secrets and no business logic — deliberately. Do not
add any. If the app ever moves to a mapping-capable region (or gets a real
HTTPS load balancer), this whole directory is deleted and app.uallak.com is
mapped straight at `super-system`.

HTTP only, on purpose: the app has no WebSocket or SSE endpoint (verified —
nothing in the codebase opens one). If that ever changes, this proxy must
grow a websocket route or the feature will silently fail behind the domain.
"""
import os
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

BACKEND_URL = os.environ.get(
    "BACKEND_URL", "https://super-system-21220555911.me-west1.run.app").rstrip("/")
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "https://app.uallak.com").rstrip("/")
BACKEND_ORIGIN = "{0.scheme}://{0.netloc}".format(urlsplit(BACKEND_URL))

# The proposal pipeline legitimately runs for minutes. Keep the read timeout
# just under Cloud Run's own request timeout (set --timeout=300 on deploy) so
# a slow backend surfaces as the backend's error rather than ours.
TIMEOUT = httpx.Timeout(connect=10.0, read=290.0, write=60.0, pool=10.0)

# RFC 9110 hop-by-hop headers: meaningful only on a single connection, never
# forwarded by an intermediary.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
}

_client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)


def _forward_request_headers(request: Request) -> dict:
    """Client headers minus hop-by-hop and Host — httpx sets Host from the
    backend URL, which is what Cloud Run's ingress routes on."""
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP and key.lower() != "host"
    }
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = request.client.host if request.client else ""
    headers["x-forwarded-for"] = (
        f"{forwarded_for}, {client_ip}" if forwarded_for and client_ip
        else forwarded_for or client_ip
    )
    # The backend builds no URLs from these today (it uses PUBLIC_APP_URL),
    # but any framework or future code that does must see the public origin,
    # not the run.app one.
    headers["x-forwarded-host"] = request.headers.get("host", "")
    # Read off the incoming header, NOT request.url.scheme: Cloud Run
    # terminates TLS upstream of us, so uvicorn's own scheme is "http" unless
    # its proxy-header trust is widened — and reporting "http" here would be a
    # lie that any future scheme-sensitive code downstream would act on.
    headers["x-forwarded-proto"] = request.headers.get("x-forwarded-proto", "https")
    return {key: value for key, value in headers.items() if value}


_HOP_BY_HOP_BYTES = {name.encode("latin-1") for name in HOP_BY_HOP}
_BACKEND_ORIGIN_BYTES = BACKEND_ORIGIN.encode("latin-1")
_PUBLIC_ORIGIN_BYTES = PUBLIC_ORIGIN.encode("latin-1")
_REDIRECT_HEADERS = {b"location", b"content-location"}


def _response_headers(upstream: httpx.Response) -> list:
    """Kept as the raw byte pairs httpx received: a login response carries
    more than one Set-Cookie (a dict would drop all but the last), and not
    round-tripping through str avoids guessing a header's encoding."""
    headers = []
    for key, value in upstream.headers.raw:
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_BYTES:
            continue
        if lowered in _REDIRECT_HEADERS and value.startswith(_BACKEND_ORIGIN_BYTES):
            # A redirect to the raw run.app host would bounce the visitor off
            # the domain — and out of their host-only session cookie.
            value = _PUBLIC_ORIGIN_BYTES + value[len(_BACKEND_ORIGIN_BYTES):]
        headers.append((lowered, value))
    return headers


def _backend_url(request: Request) -> str:
    """Prefer ASGI's raw_path: request.url.path is already percent-DECODED, so
    rebuilding from it round-trips every escape through a second encoder. The
    app's paths are plain ASCII today, but a proxy that quietly mangles a URL
    is a bad thing to own."""
    raw_path = request.scope.get("raw_path") or b""
    raw_path = raw_path.split(b"?", 1)[0]  # some servers include the query here
    path = raw_path.decode("ascii", "ignore") if raw_path else request.url.path
    return BACKEND_URL + path + (f"?{request.url.query}" if request.url.query else "")


async def proxy(request: Request) -> StreamingResponse:
    url = _backend_url(request)

    upstream_request = _client.build_request(
        request.method, url,
        headers=_forward_request_headers(request),
        content=await request.body(),
    )
    try:
        upstream = await _client.send(upstream_request, stream=True)
    except httpx.RequestError as e:
        return PlainTextResponse(f"upstream unreachable: {e}", status_code=502)

    # aiter_raw (not aiter_bytes): the body stays exactly as the backend
    # encoded it, so its own Content-Encoding/Content-Length stay truthful.
    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose),
    )
    # Assigned after construction: StreamingResponse builds its own minimal
    # header set, and the upstream's headers must win wholesale.
    response.raw_headers = _response_headers(upstream)
    return response


async def health(request: Request) -> PlainTextResponse:
    """Proxy-only liveness. The app's own /health passes through untouched."""
    return PlainTextResponse("ok")


app = Starlette(routes=[
    Route("/_proxy/health", health, methods=["GET"]),
    Route("/{path:path}", proxy,
          methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
])
