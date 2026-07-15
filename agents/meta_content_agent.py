"""uallak's Meta ORGANIC content agent — the Pages/Instagram Graph API side of
the Meta integration. Paid campaigns live in agents/meta_ads_agent.py; both
share the OAuth connection made in the dashboard and core/meta_service.py.

This is the "pipe" between media/content generation and the client's actual
Facebook Page / Instagram account:
- publish(): takes ALREADY-GENERATED media (public URLs) and posts it — this
  agent never generates content itself
- inbox: reads comments + Page DMs, surfaces new ones to the team (v1 replies
  are human-triggered via reply_to_comment; no autonomous LLM replies yet —
  a wrong public reply on a client's brand page is worse than a slow one)
- engagement tracking: likes/comments/shares summary, fed into the Meta weekly
  report and client_activity

No LLM calls here at all — this agent only talks to the Graph API.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import meta_service as meta
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "meta_content_agent"
PAGE_PLATFORM = "meta_page"            # account_id=Page id, access_token=Page token
INSTAGRAM_PLATFORM = "meta_instagram"  # account_id=IG business id, same Page token

VALID_TARGETS = ("facebook", "instagram")
# facebook: text | link | photo | video (FB Reels need a separate resumable-upload
# flow — deferred). instagram: photo | reel | story (IG feed video IS a reel now).
VALID_KINDS = {
    "facebook": ("text", "link", "photo", "video"),
    "instagram": ("photo", "reel", "story"),
}

# IG publishes via a container that Meta processes server-side; videos can take
# a couple of minutes. Endpoints calling publish() must be plain `def` (threadpool).
IG_CONTAINER_POLL_SECONDS = 5
IG_CONTAINER_POLL_MAX_TRIES = 24  # ~2 minutes

INBOX_LOOKBACK_DAYS = 7
ENGAGEMENT_WINDOW_DAYS = 7
RECENT_POSTS_LIMIT = 20

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _get_connection(client_id: int, platform: str) -> dict:
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", platform)
        .eq("status", "active")
        # newest row wins; client_accounts has no created_at column, so order
        # by the auto-incrementing id (same semantics)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def is_connected(client_id: int) -> bool:
    page = _get_connection(client_id, PAGE_PLATFORM)
    return bool(page.get("access_token") and page.get("account_id"))


def page_connected_client_ids() -> list:
    """client_ids of every active Page connection (used by the Meta weekly
    report to include page-only clients)."""
    result = (
        _db().table("client_accounts")
        .select("client_id")
        .eq("platform", PAGE_PLATFORM)
        .eq("status", "active")
        .execute()
    )
    return sorted({row["client_id"] for row in (result.data or [])})


# ─── Publishing ───────────────────────────────────────────────────────────────

def _validate_publish_spec(spec: dict) -> list:
    errors = []
    target, kind = spec.get("target", ""), spec.get("kind", "")
    if target not in VALID_TARGETS:
        errors.append(f"target must be one of {VALID_TARGETS}")
        return errors  # kind validation is meaningless without a valid target
    if kind not in VALID_KINDS[target]:
        errors.append(f"kind for {target} must be one of {VALID_KINDS[target]}")
        return errors

    media_url = spec.get("media_url", "")
    needs_media = kind in ("photo", "video", "reel", "story")
    if needs_media and not media_url.startswith(("http://", "https://")):
        errors.append(f"kind '{kind}' requires media_url as a PUBLIC http(s) URL "
                      "(Meta fetches it server-side)")
    if kind == "text" and not (spec.get("message") or "").strip():
        errors.append("kind 'text' requires a message")
    if kind == "link" and not (spec.get("link") or "").startswith(("http://", "https://")):
        errors.append("kind 'link' requires link as an http(s) URL")
    return errors


def _publish_facebook(page: dict, spec: dict) -> dict:
    token, page_id = page["access_token"], page["account_id"]
    kind, message = spec["kind"], (spec.get("message") or "").strip()

    if kind in ("text", "link"):
        data = {"message": message}
        if spec.get("link"):
            data["link"] = spec["link"]
        result = meta.graph_post(f"{page_id}/feed", token, data=data)
    elif kind == "photo":
        result = meta.graph_post(f"{page_id}/photos", token,
                                 data={"url": spec["media_url"], "caption": message})
    else:  # video
        result = meta.graph_post(f"{page_id}/videos", token,
                                 data={"file_url": spec["media_url"], "description": message})
    return {"post_id": result.get("post_id") or result.get("id", "")}


def _publish_instagram(ig: dict, spec: dict) -> dict:
    token, ig_id = ig["access_token"], ig["account_id"]
    kind, caption = spec["kind"], (spec.get("message") or "").strip()
    is_video = spec["media_url"].lower().split("?")[0].endswith((".mp4", ".mov"))

    container_spec = {"caption": caption} if caption else {}
    if kind == "photo":
        container_spec["image_url"] = spec["media_url"]
    elif kind == "reel":
        container_spec["media_type"] = "REELS"
        container_spec["video_url"] = spec["media_url"]
    else:  # story — image or video, Meta decides by which URL field is set
        container_spec["media_type"] = "STORIES"
        container_spec["video_url" if is_video else "image_url"] = spec["media_url"]

    container_id = meta.graph_post(f"{ig_id}/media", token, data=container_spec)["id"]

    # Meta processes the media server-side; publish only once the container is
    # FINISHED (images are usually instant, videos take a minute or two)
    for _ in range(IG_CONTAINER_POLL_MAX_TRIES):
        status = meta.graph_get(f"{container_id}", token,
                                params={"fields": "status_code"}).get("status_code", "")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"IG container {container_id} failed processing "
                               "(is the media URL public and a supported format?)")
        time.sleep(IG_CONTAINER_POLL_SECONDS)
    else:
        raise RuntimeError(f"IG container {container_id} still processing after "
                           f"{IG_CONTAINER_POLL_MAX_TRIES * IG_CONTAINER_POLL_SECONDS}s - "
                           "publish it manually or retry")

    result = meta.graph_post(f"{ig_id}/media_publish", token, data={"creation_id": container_id})
    return {"post_id": result.get("id", "")}


def publish(client_id: int, spec: dict) -> dict:
    """Publish one piece of already-generated content to the client's connected
    Facebook Page or Instagram account.

    spec: {target: facebook|instagram, kind: see VALID_KINDS,
           message?: post text / caption, media_url?: PUBLIC http(s) URL,
           link?: for facebook 'link' kind}
    """
    from agents.client_agent import log_activity

    errors = _validate_publish_spec(spec)
    if errors:
        return {"success": False, "errors": errors}

    target = spec["target"]
    conn = _get_connection(client_id,
                           PAGE_PLATFORM if target == "facebook" else INSTAGRAM_PLATFORM)
    if not (conn.get("access_token") and conn.get("account_id")):
        return {"success": False, "errors": [f"no connected {target} account"]}

    log_step(AGENT_NAME, "publish", f"client_id={client_id} target={target} kind={spec['kind']}")
    try:
        result = timed_step(
            AGENT_NAME, f"publish_{target}",
            lambda: (_publish_facebook if target == "facebook" else _publish_instagram)(conn, spec),
        )
    except Exception as e:
        agent_alert(AGENT_NAME, [f"publish failed for client {client_id} "
                                 f"({target}/{spec['kind']}): {e}"])
        return {"success": False, "errors": [str(e)]}

    log_activity(client_id, AGENT_NAME, "content_published",
                 {"target": target, "kind": spec["kind"]}, result)
    return {"success": True, "target": target, **result}


# ─── Inbox: comments + Page DMs ───────────────────────────────────────────────

def _facebook_comments(page: dict, since: datetime) -> list:
    """Recent comments by OTHERS on the Page's recent posts."""
    token, page_id = page["access_token"], page["account_id"]
    posts = meta.graph_get(
        f"{page_id}/posts", token,
        params={"fields": "id,message,created_time,"
                          "comments.limit(25){id,message,from,created_time}",
                "limit": RECENT_POSTS_LIMIT},
    ).get("data", [])

    comments = []
    for post in posts:
        for comment in (post.get("comments") or {}).get("data", []):
            author = comment.get("from") or {}
            if author.get("id") == page_id:  # our own replies aren't inbox items
                continue
            created = comment.get("created_time", "")
            if created and created < since.strftime("%Y-%m-%dT%H:%M:%S+0000"):
                continue
            comments.append({
                "platform": "facebook",
                "comment_id": comment.get("id", ""),
                "post_id": post.get("id", ""),
                "post_excerpt": (post.get("message") or "")[:80],
                "author": author.get("name", ""),
                "text": comment.get("message", ""),
                "created_time": created,
            })
    return comments


def _instagram_comments(ig: dict, since: datetime) -> list:
    token, ig_id = ig["access_token"], ig["account_id"]
    own_username = meta.graph_get(f"{ig_id}", token,
                                  params={"fields": "username"}).get("username", "")
    media = meta.graph_get(
        f"{ig_id}/media", token,
        params={"fields": "id,caption,timestamp,"
                          "comments.limit(25){id,text,username,timestamp}",
                "limit": RECENT_POSTS_LIMIT},
    ).get("data", [])

    comments = []
    for item in media:
        for comment in (item.get("comments") or {}).get("data", []):
            if comment.get("username", "") == own_username:
                continue
            created = comment.get("timestamp", "")
            if created and created < since.strftime("%Y-%m-%dT%H:%M:%S+0000"):
                continue
            comments.append({
                "platform": "instagram",
                "comment_id": comment.get("id", ""),
                "post_id": item.get("id", ""),
                "post_excerpt": (item.get("caption") or "")[:80],
                "author": comment.get("username", ""),
                "text": comment.get("text", ""),
                "created_time": created,
            })
    return comments


def _page_messages(page: dict) -> list:
    """Page conversations with unread messages (Messenger; IG DMs deferred)."""
    token, page_id = page["access_token"], page["account_id"]
    conversations = meta.graph_get(
        f"{page_id}/conversations", token,
        params={"fields": "id,updated_time,unread_count,senders,"
                          "messages.limit(3){message,from,created_time}",
                "limit": 25},
    ).get("data", [])

    messages = []
    for conv in conversations:
        if not conv.get("unread_count"):
            continue
        latest = ((conv.get("messages") or {}).get("data") or [{}])[0]
        senders = [s.get("name", "") for s in (conv.get("senders") or {}).get("data", [])]
        messages.append({
            "conversation_id": conv.get("id", ""),
            "updated_time": conv.get("updated_time", ""),
            "unread_count": conv.get("unread_count", 0),
            "sender": next((s for s in senders if s), ""),
            "latest_message": (latest.get("message") or "")[:200],
        })
    return messages


def get_inbox(client_id: int, lookback_days: int = INBOX_LOOKBACK_DAYS) -> dict:
    """Recent comments (FB + IG) and unread Page DMs, for the team to act on.
    Always returns a well-formed dict — partial failures are reported inline."""
    page = _get_connection(client_id, PAGE_PLATFORM)
    ig = _get_connection(client_id, INSTAGRAM_PLATFORM)
    if not (page.get("access_token") and page.get("account_id")):
        return {"connected": False}

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    log_step(AGENT_NAME, "get_inbox", f"client_id={client_id} lookback={lookback_days}d")
    inbox = {"connected": True, "comments": [], "messages": [], "errors": []}

    for label, fetch in (
        ("facebook comments", lambda: _facebook_comments(page, since)),
        ("instagram comments",
         (lambda: _instagram_comments(ig, since)) if ig.get("account_id") else (lambda: [])),
    ):
        try:
            inbox["comments"].extend(fetch())
        except Exception as e:
            inbox["errors"].append(f"{label}: {e}")
    try:
        inbox["messages"] = _page_messages(page)
    except Exception as e:
        # pages_messaging is deliberately absent from Phase 1's OAUTH_SCOPES
        # (see core/meta_service.py) - a Graph permission error here is the
        # expected steady state until Advanced Access, not alert-worthy.
        if isinstance(e, meta.MetaGraphError) and e.code in (10, 200):
            inbox["messages_note"] = "Page DMs unavailable (pages_messaging not granted in Phase 1)"
        else:
            inbox["errors"].append(f"page messages: {e}")

    if inbox["errors"]:
        agent_alert(AGENT_NAME, [f"inbox fetch for client {client_id} had errors: "
                                 f"{'; '.join(inbox['errors'])}"])
    inbox["comments"].sort(key=lambda c: c.get("created_time", ""), reverse=True)
    return inbox


def reply_to_comment(client_id: int, comment_id: str, message: str,
                     target: str = "facebook") -> dict:
    """Post a reply to one comment, as the Page / IG account. Human-triggered
    (admin flow) — this agent never auto-replies on a client's brand page."""
    from agents.client_agent import log_activity

    if target not in VALID_TARGETS:
        return {"success": False, "error": f"target must be one of {VALID_TARGETS}"}
    if not (message or "").strip():
        return {"success": False, "error": "message is required"}

    conn = _get_connection(client_id,
                           PAGE_PLATFORM if target == "facebook" else INSTAGRAM_PLATFORM)
    if not conn.get("access_token"):
        return {"success": False, "error": f"no connected {target} account"}

    log_step(AGENT_NAME, "reply_to_comment", f"client_id={client_id} comment_id={comment_id}")
    try:
        # FB replies are comments-on-a-comment; IG has a dedicated replies edge
        path = f"{comment_id}/comments" if target == "facebook" else f"{comment_id}/replies"
        result = meta.graph_post(path, conn["access_token"], data={"message": message.strip()})
    except Exception as e:
        agent_alert(AGENT_NAME, [f"comment reply failed for client {client_id} "
                                 f"({target}, comment {comment_id}): {e}"])
        return {"success": False, "error": str(e)}

    log_activity(client_id, AGENT_NAME, "comment_replied",
                 {"target": target, "comment_id": comment_id}, {"reply_id": result.get("id", "")})
    return {"success": True, "reply_id": result.get("id", "")}


# ─── Scheduled inbox scan ─────────────────────────────────────────────────────

def _surfaced_item_keys(client_id: int) -> set:
    """Item keys already surfaced to the team, from client_activity (durable
    dedup — survives redeploys, same pattern as the ads agents' issue dedup)."""
    from agents.client_agent import get_activity
    keys = set()
    for entry in get_activity(client_id, limit=100):
        if entry.get("action_type") != "content_inbox_surfaced":
            continue
        keys.update((entry.get("details") or {}).get("item_keys", []))
    return keys


def run_inbox_scan() -> dict:
    """Scan every connected Page for NEW comments/unread DMs and alert the team
    once per item. Designed for a Cloud Scheduler hit on /api/meta-content/scan."""
    from agents.client_agent import log_activity

    client_ids = page_connected_client_ids()
    log_step(AGENT_NAME, "run_inbox_scan", f"{len(client_ids)} connected pages")
    summary = {"clients_scanned": 0, "new_comments": 0, "new_messages": 0}

    for client_id in client_ids:
        try:
            inbox = get_inbox(client_id)
            if not inbox.get("connected"):
                continue
            seen = _surfaced_item_keys(client_id)

            new_comments = [c for c in inbox["comments"]
                            if c["comment_id"] and c["comment_id"] not in seen]
            # A conversation resurfaces when a NEW message arrives (updated_time moves)
            new_messages = [m for m in inbox["messages"]
                            if f"{m['conversation_id']}:{m['updated_time']}" not in seen]
            if not new_comments and not new_messages:
                summary["clients_scanned"] += 1
                continue

            lines = [f"client {client_id}: {len(new_comments)} new comment(s), "
                     f"{len(new_messages)} unread DM conversation(s) need a human look"]
            lines += [f"  [{c['platform']}] {c['author']}: \"{c['text'][:100]}\""
                      for c in new_comments[:5]]
            lines += [f"  [DM] {m['sender']}: \"{m['latest_message'][:100]}\""
                      for m in new_messages[:5]]
            agent_alert(AGENT_NAME, lines)

            item_keys = ([c["comment_id"] for c in new_comments]
                         + [f"{m['conversation_id']}:{m['updated_time']}" for m in new_messages])
            log_activity(client_id, AGENT_NAME, "content_inbox_surfaced",
                         {"item_keys": item_keys},
                         {"comments": len(new_comments), "messages": len(new_messages)})
            summary["new_comments"] += len(new_comments)
            summary["new_messages"] += len(new_messages)
            summary["clients_scanned"] += 1
        except Exception as e:
            agent_alert(AGENT_NAME, [f"inbox scan failed for client {client_id}: {e}"])

    log_step(AGENT_NAME, "run_inbox_scan",
             f"done - {summary['clients_scanned']} scanned, "
             f"{summary['new_comments']} comments, {summary['new_messages']} DMs surfaced")
    return summary


# ─── Engagement tracking ──────────────────────────────────────────────────────

def get_engagement_summary(client_id: int, window_days: int = ENGAGEMENT_WINDOW_DAYS) -> dict:
    """Organic engagement totals (posts published in the window + their likes/
    comments/shares) for FB and IG. Feeds the Meta weekly report."""
    page = _get_connection(client_id, PAGE_PLATFORM)
    ig = _get_connection(client_id, INSTAGRAM_PLATFORM)
    if not (page.get("access_token") and page.get("account_id")):
        return {"connected": False}

    since_str = (datetime.now(timezone.utc) - timedelta(days=window_days)) \
        .strftime("%Y-%m-%dT%H:%M:%S+0000")
    summary = {"connected": True, "period_days": window_days}

    try:
        posts = meta.graph_get(
            f"{page['account_id']}/posts", page["access_token"],
            params={"fields": "id,created_time,shares,"
                              "likes.limit(0).summary(true),comments.limit(0).summary(true)",
                    "limit": RECENT_POSTS_LIMIT},
        ).get("data", [])
        recent = [p for p in posts if p.get("created_time", "") >= since_str]
        summary["facebook"] = {
            "posts": len(recent),
            "likes": sum(((p.get("likes") or {}).get("summary") or {}).get("total_count", 0)
                         for p in recent),
            "comments": sum(((p.get("comments") or {}).get("summary") or {}).get("total_count", 0)
                            for p in recent),
            "shares": sum((p.get("shares") or {}).get("count", 0) for p in recent),
        }
    except Exception as e:
        print(f"[{AGENT_NAME}] facebook engagement fetch failed for client {client_id}: {e}")

    if ig.get("account_id"):
        try:
            media = meta.graph_get(
                f"{ig['account_id']}/media", ig["access_token"],
                params={"fields": "id,timestamp,like_count,comments_count",
                        "limit": RECENT_POSTS_LIMIT},
            ).get("data", [])
            recent = [m for m in media if m.get("timestamp", "") >= since_str]
            summary["instagram"] = {
                "posts": len(recent),
                "likes": sum(int(m.get("like_count", 0) or 0) for m in recent),
                "comments": sum(int(m.get("comments_count", 0) or 0) for m in recent),
            }
        except Exception as e:
            print(f"[{AGENT_NAME}] instagram engagement fetch failed for client {client_id}: {e}")

    return summary
