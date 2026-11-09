"""Drive → TikTok inbox uploads as DURABLE, resumable jobs (2026-11-10).

Owner's policy (Johnny):
- up to `tiktok_auto_upload_max_mb` (admin setting, default 1536 = 1.5 GB):
  the client's own card click is all it takes — no size question, no person;
- above it: never refused and the client is never asked. ONE approval request
  goes to the owner — the admin bell (dashboard/assets/frustration-widget.js)
  and ONE email to ADMIN_EMAIL with client, file, size and Approve / Decline.
  The client reads only a calm "I'm on it" line. Approve is FINAL: the upload
  runs with no further cap or question. Decline: the client is told, plainly,
  what was decided and the next step.

Why a job and not one long request or a background task:
- a FastAPI background task runs AFTER the response, and Cloud Run (request-
  based CPU, no --no-cpu-throttling, no min instances — owner's rule) gives it
  almost no CPU and may drop it with the instance;
- one request is cut at the ~300 s request timeout (proxy read 290 s).
So the upload is a sequence of steps, each INSIDE an active request, with
progress saved after every chunk:
- the Cloud Scheduler job `tiktok-uploads` (every minute, GET
  /api/tiktok/uploads/run, X-Admin-Key) runs due jobs for up to TICK_BUDGET_S;
- the card's own poll (agent_actions.status_card) runs a short NUDGE while
  the client watches.
Never a FastAPI background task: it would hold the lease with throttled CPU.
A per-client lease (`tiktok_upload_lock:<id>`, insert-to-acquire, expiring)
means one worker per client at a time. Memory stays flat for any size: one
chunk (16 MB, core/video_limits) in memory, read from Drive by range.
TikTok's upload_url lives 1 hour: a session older than URL_REFRESH_S starts
over with a new init (bounded restarts).

Failures: each chunk has 3 retries (tiktok_service.upload_chunk); a step that
still fails is retried later (MAX_ATTEMPTS, backoff), then the job ends on OUR
side — one admin alert, and the client reads a calm line, never the error.
Explained stops (reconnect TikTok, too many drafts waiting, a file TikTok
cannot decode) keep their own plain refusal lines. TikTok's limits (size,
length, ...) are never checked here.

Storage: one app_settings row per client, `tiktok_upload:<id>` = {"jobs": [...]}
(no migration). The row holds TikTok's upload_url while a session is open —
never printed or logged; no token is ever stored here. client_deletion clears
both keys; admin_service.get_settings skips them.
"""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "tiktok_uploads"
PREFIX = "tiktok_upload:"
LOCK_PREFIX = "tiktok_upload_lock:"
TICK_BUDGET_S = 200            # scheduler request: under the 290 s proxy read timeout
NUDGE_BUDGET_S = 20            # the card's poll request
CHUNK_MARGIN_S = 25            # don't start a chunk with less than this left
LEASE_S = TICK_BUDGET_S + 120
URL_REFRESH_S = 50 * 60        # TikTok's upload_url lives 1 h
MAX_RESTARTS = 3
MAX_ATTEMPTS = 4
RETRY_AFTER_S = (30, 120, 300, 600)
PROCESSING_MAX_S = 30 * 60
CARD_JOB_MAX_S = 55 * 60       # a card's job ends before the card's reaper (agent_actions slow 30 min)
STATUS_POLL_S = 5
PROCESSING_WAIT_S = 45
PROGRESS_MIN_BYTES = 256 * 1024 * 1024
PROGRESS_MARKS = (25, 50, 75)
KEEP_FINISHED = 15
MAX_PARALLEL = 2

ACTIVE = ("queued", "uploading", "processing")
OPEN = ("awaiting_admin",) + ACTIVE
_parallel = threading.BoundedSemaphore(MAX_PARALLEL)
_HOLDER = uuid.uuid4().hex[:10]


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt=None) -> str:
    return (dt or _now()).isoformat()


def _age(value) -> float:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (_now() - when).total_seconds()
    except (TypeError, ValueError):
        return 10 ** 9


def _log(message: str, severity: str = "INFO", **fields):
    from core.agent_base import log_step
    log_step(SERVICE_NAME, "job", message)


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


# ─── Client-facing lines (plain; no tool names, never an error) ─────────────

def _t(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def _pick(texts: dict, lang: str) -> str:
    return texts.get(lang) or texts["he"]


ON_IT = _t("אני מטפל בזה, אעדכן כשמוכן.", "I'm on it — I'll update you when it's ready.",
           "Je m'en occupe, je vous tiens au courant dès que c'est prêt.",
           "أنا أتولى الأمر، وسأبلغكم عندما يجهز.", "Я этим занимаюсь, сообщу, когда будет готово.")
PROGRESS = _t("\"{file}\" בדרך — {pct}% כבר הועלו.", "\"{file}\" is on its way — {pct}% uploaded so far.",
              "« {file} » est en route — {pct}% déjà envoyés.", "\"{file}\" في الطريق — تم رفع {pct}% حتى الآن.",
              "«{file}» в пути — загружено {pct}%.")
STILL_ON_IT = _t("ההעלאה של \"{file}\" לוקחת יותר זמן מהרגיל. אני ממשיך לטפל בזה ואעדכן כאן.",
                 "Uploading \"{file}\" is taking longer than usual. I'm still on it and will update you here.",
                 "L'envoi de « {file} » prend plus de temps que d'habitude. Je continue et je vous tiens au courant ici.",
                 "رفع \"{file}\" يستغرق وقتًا أطول من المعتاد. ما زلت أتابع الأمر وسأبلغكم هنا.",
                 "Загрузка «{file}» занимает больше времени, чем обычно. Я продолжаю и сообщу здесь.")
DECLINED = _t("החלטנו לא להעלות כרגע את \"{file}\" לטיוטות בטיקטוק, כי הקובץ גדול מאוד. אם תשמרו בתיקיית המדיה גרסה קצרה או דחוסה יותר ותכתבו לי, אשלח אותה מיד.",
              "We decided not to send \"{file}\" to your TikTok drafts for now, because the file is very large. Save a shorter or more compressed version in your media folder, tell me, and I'll send it right away.",
              "Nous avons décidé de ne pas envoyer « {file} » dans vos brouillons TikTok pour l'instant, car le fichier est très volumineux. Enregistrez une version plus courte ou plus compressée dans votre dossier média, dites-le-moi, et je l'envoie aussitôt.",
              "قررنا عدم إرسال \"{file}\" إلى مسودات تيك توك حاليًا لأن الملف كبير جدًا. احفظوا نسخة أقصر أو مضغوطة أكثر في مجلد الوسائط وأخبروني، وسأرسلها فورًا.",
              "Мы решили пока не отправлять «{file}» в черновики TikTok — файл очень большой. Сохраните более короткую или сжатую версию в папке медиа, напишите мне, и я сразу её отправлю.")


# ─── Storage ────────────────────────────────────────────────────────────────

def _key(client_id) -> str:
    return f"{PREFIX}{int(client_id)}"


def load(client_id: int) -> list:
    rows = (_db().table("app_settings").select("value").eq("key", _key(client_id)).limit(1)
            .execute().data) or []
    value = rows[0].get("value") if rows else {}
    jobs = (value or {}).get("jobs") if isinstance(value, dict) else None
    return list(jobs or [])


def _save(client_id: int, jobs: list):
    finished = [j for j in jobs if j.get("status") not in OPEN]
    keep = [j for j in jobs if j.get("status") in OPEN] + finished[-KEEP_FINISHED:]
    keep.sort(key=lambda j: j.get("created_at") or "")
    _db().table("app_settings").upsert({"key": _key(client_id), "value": {"jobs": keep},
                                        "updated_at": _iso()}, on_conflict="key").execute()


def _update(client_id: int, job_id: str, **changes) -> dict:
    """Fresh read → change ONE job → write (never a stale whole-list overwrite)."""
    jobs = load(client_id)
    for job in jobs:
        if job.get("id") == job_id:
            job.update(changes, updated_at=_iso())
            _save(client_id, jobs)
            return job
    return {}


def get(client_id: int, job_id: str) -> dict:
    return next((j for j in load(client_id) if j.get("id") == job_id), {})


def _new_job(client_id, file_id, file_name, size, persona, lang, status, proposal_id=None) -> dict:
    return {"id": uuid.uuid4().hex[:12], "client_id": int(client_id), "file_id": file_id,
            "file_name": file_name or "", "size": int(size or 0), "status": status,
            "persona": persona or "media", "lang": lang or "he", "proposal_id": proposal_id,
            "needs_owner": status == "awaiting_admin", "created_at": _iso(), "updated_at": _iso(),
            "next": 0, "chunks": 0, "attempts": 0, "restarts": 0, "posted_pct": 0}


def create(client_id, file_id, file_name, size, persona="media", lang="he", proposal_id=None,
           status="queued") -> dict:
    jobs = load(client_id)
    job = _new_job(client_id, file_id, file_name, size, persona, lang, status, proposal_id)
    jobs.append(job)
    _save(client_id, jobs)
    _log(f"client {client_id}: TikTok upload job {job['id']} {status} ({round(job['size'] / 1048576)} MB)")
    return job


# ─── Lease: one worker per client ───────────────────────────────────────────

def _lease_take(client_id) -> str:
    key, token = f"{LOCK_PREFIX}{int(client_id)}", f"{_HOLDER}:{uuid.uuid4().hex[:6]}"
    value = {"token": token, "until": _iso(_now() + timedelta(seconds=LEASE_S))}
    for _ in range(2):
        try:
            _db().table("app_settings").insert({"key": key, "value": value, "updated_at": _iso()}).execute()
            return token
        except Exception:
            rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
            held = (rows[0].get("value") if rows else {}) or {}
            if rows and _age(held.get("until")) < 0:
                return ""              # someone is working on it
            # expired (or vanished): remove THAT lease only, then try once more
            q = _db().table("app_settings").delete().eq("key", key)
            if held.get("token"):
                q = q.eq("value->>token", held["token"])
            q.execute()
    return ""


def _lease_release(client_id, token):
    try:
        (_db().table("app_settings").delete().eq("key", f"{LOCK_PREFIX}{int(client_id)}")
         .eq("value->>token", token).execute())
    except Exception:
        pass


# ─── The owner's approval (files above the threshold) ───────────────────────

def request_owner_approval(client_id: int, file_id: str, file_name: str, size: int,
                           persona: str = "media", lang: str = "he") -> dict:
    """ONE request per file: an open job for the same file is returned as is
    (no second email, no second bell item)."""
    for job in load(client_id):
        if job.get("file_id") == file_id and job.get("status") in OPEN:
            return job
    job = create(client_id, file_id, file_name, size, persona, lang, status="awaiting_admin")
    _notify_owner(job)
    return job


def _client_name(client_id) -> str:
    try:
        from agents.client_agent import get_client
        c = get_client(client_id) or {}
        return c.get("business_name") or c.get("name") or f"#{client_id}"
    except Exception:
        return f"#{client_id}"


def _notify_owner(job: dict):
    import os
    base = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")
    ref = f"{job['client_id']}:{job['id']}"
    mb = round(job["size"] / 1048576)
    name = _client_name(job["client_id"])
    try:
        from core.email_service import send_owner_action
        send_owner_action(
            f"אישור העלאה לטיקטוק: {name} — {mb}MB",
            f"הלקוח {name} (#{job['client_id']}) ביקש לשלוח לטיוטות בטיקטוק את \"{job['file_name']}\" "
            f"בגודל {mb}MB — מעל סף ההעלאה האוטומטית. ללקוח נאמר רק שאנחנו מטפלים. "
            f"אישור = ההעלאה רצה בלי תקרה נוספת. דחייה = הלקוח מקבל הסבר והצעה לשלוח גרסה קטנה יותר.",
            [("אישור", f"{base}/admin/?tiktok_upload={ref}&decision=approve"),
             ("דחייה", f"{base}/admin/?tiktok_upload={ref}&decision=decline")])
    except Exception as e:
        _log(f"client {job['client_id']}: owner email for job {job['id']} failed ({type(e).__name__})",
             severity="WARNING")


def pending_approvals() -> list:
    out = []
    for row in _all_rows():
        for job in row:
            if job.get("status") == "awaiting_admin":
                out.append({**_public(job), "client_name": _client_name(job["client_id"])})
    return sorted(out, key=lambda j: j.get("created_at") or "")


def _public(job: dict) -> dict:
    """What the admin screen shows — never the upload_url."""
    keys = ("id", "client_id", "file_name", "size", "status", "created_at", "updated_at", "next", "chunks",
            "attempts", "restarts", "needs_owner", "approved_by", "approved_at", "declined_by", "outcome")
    return {k: job.get(k) for k in keys}


def decide(client_id: int, job_id: str, decision: str, who: str) -> dict:
    job = get(client_id, job_id)
    if not job:
        return {"error": "not_found"}
    if job.get("status") != "awaiting_admin":
        return {"error": "already_decided", "job": _public(job)}
    if decision == "approve":
        job = _update(client_id, job_id, status="queued", approved_by=who[:80], approved_at=_iso())
        _log(f"client {client_id}: owner approved TikTok upload job {job_id} ({who})")
        return {"job": _public(job)}
    if decision == "decline":
        job = _update(client_id, job_id, status="declined", declined_by=who[:80], outcome="declined")
        _log(f"client {client_id}: owner declined TikTok upload job {job_id} ({who})")
        _say(job, _pick(DECLINED, job.get("lang")).replace("{file}", job.get("file_name") or "—"))
        return {"job": _public(job)}
    return {"error": "bad_decision"}


# ─── Running ────────────────────────────────────────────────────────────────

def _all_rows() -> list:
    rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%")
            .execute().data) or []
    out = []
    for row in rows:
        value = row.get("value") or {}
        out.append(list(value.get("jobs") or []) if isinstance(value, dict) else [])
    return out


def _due(job: dict) -> bool:
    if job.get("status") not in ACTIVE:
        return False
    return not job.get("retry_at") or _age(job.get("retry_at")) >= 0


def run_due(budget_s: float = TICK_BUDGET_S) -> dict:
    """The scheduler tick: every client with a due job, one after another,
    inside this request's budget."""
    deadline = time.monotonic() + budget_s
    seen, advanced = 0, 0
    for jobs in _all_rows():
        due = [j for j in jobs if _due(j)]
        if not due:
            continue
        seen += 1
        if time.monotonic() > deadline - CHUNK_MARGIN_S:
            break
        advanced += step_client(due[0]["client_id"], deadline)
    return {"clients_with_due_jobs": seen, "steps": advanced}


def nudge(client_id: int, budget_s: float = NUDGE_BUDGET_S) -> int:
    """A short step while the client's card is being watched. Never raises."""
    try:
        if not any(_due(j) for j in load(client_id)):
            return 0
        return step_client(client_id, time.monotonic() + budget_s)
    except Exception as e:
        _log(f"client {client_id}: nudge failed ({type(e).__name__})", severity="WARNING")
        return 0


def step_client(client_id: int, deadline: float) -> int:
    if not _parallel.acquire(blocking=False):
        return 0
    try:
        token = _lease_take(client_id)
        if not token:
            return 0
        try:
            steps = 0
            while time.monotonic() < deadline - CHUNK_MARGIN_S:
                job = next((j for j in load(client_id) if _due(j)), None)
                if not job:
                    break
                before = (job.get("status"), job.get("next"))
                _advance(client_id, job, deadline)
                steps += 1
                after = get(client_id, job["id"])
                if (after.get("status"), after.get("next")) == before:
                    break                     # waiting on TikTok / a retry later
            return steps
        finally:
            _lease_release(client_id, token)
    finally:
        _parallel.release()


def _advance(client_id: int, job: dict, deadline: float):
    from agents import tiktok_content_agent as agent
    job_id = job["id"]
    limit = CARD_JOB_MAX_S if job.get("proposal_id") else None
    if limit and _age(job.get("approved_at") or job.get("created_at")) > limit:
        return _fail(job, "card job past its time")
    try:
        if job["status"] == "queued":
            up = agent.begin_upload(client_id, job["file_id"])
            job = _update(client_id, job_id, status="uploading", publish_id=up["publish_id"],
                          upload_url=up["upload_url"], init_at=_iso(), size=up["size"],
                          chunk_size=up["chunk_size"], chunks=up["chunks"], next=0, mime=up["mime"],
                          video=up["video"], warnings=up["warnings"], attempts=0)
        if job["status"] == "uploading":
            if _age(job.get("init_at")) > URL_REFRESH_S:
                return _restart(job, "upload session older than 50 min")
            job = _send_chunks(client_id, job, deadline)
            if job.get("status") != "uploading" or job["next"] < job["chunks"]:
                return
            job = _update(client_id, job_id, status="processing", sent_at=_iso(), upload_url=None)
        if job["status"] == "processing":
            # at most PROCESSING_WAIT_S per request: other clients' jobs share the tick
            until = min(deadline, time.monotonic() + PROCESSING_WAIT_S)
            while time.monotonic() < until - STATUS_POLL_S:
                state, detail = agent.delivery_status(client_id, job["publish_id"])
                if state == "done":
                    return _done(job, detail)
                if state == "failed":
                    return _fail(job, f"TikTok processing failed: {detail}")
                if _age(job.get("sent_at")) > PROCESSING_MAX_S:
                    return _fail(job, "TikTok still processing after 30 min")
                time.sleep(STATUS_POLL_S)
    except agent.UploadRefused as e:
        return _refused(job, e.code, e.errors)
    except Exception as e:
        return _retry_later(job, e)


def _send_chunks(client_id: int, job: dict, deadline: float) -> dict:
    """Chunks from job["next"] until the budget ends; progress saved after
    each one, so the next step resumes exactly there."""
    from core import drive_storage

    @drive_storage.scoped
    def run(client_id):
        from core import drive_service as drive
        from core import tiktok_service as tiktok
        from core.video_limits import tiktok_chunk_plan
        current = job
        plan = tiktok_chunk_plan(current["size"], current["chunk_size"])

        def read(start, length):
            return drive.read_range(current["file_id"], start, length)

        index = int(current.get("next") or 0)
        while index < len(plan) and time.monotonic() < deadline - CHUNK_MARGIN_S:
            tiktok.upload_chunk(current["upload_url"], read, current["size"], index, plan,
                                current.get("mime") or "video/mp4")
            index += 1
            current = _update(client_id, current["id"], next=index, attempts=0) or {**current, "next": index}
            _maybe_progress(current)
        return current

    return run(client_id)


def _maybe_progress(job: dict):
    if job.get("size", 0) < PROGRESS_MIN_BYTES or not job.get("chunks"):
        return
    pct = int(100 * job["next"] / job["chunks"])
    mark = max([m for m in PROGRESS_MARKS if pct >= m] or [0])
    if mark > int(job.get("posted_pct") or 0):
        _update(job["client_id"], job["id"], posted_pct=mark)
        _say(job, _pick(PROGRESS, job.get("lang")).replace("{file}", job.get("file_name") or "—")
             .replace("{pct}", str(mark)))


def _restart(job: dict, why: str):
    restarts = int(job.get("restarts") or 0) + 1
    if restarts > MAX_RESTARTS:
        return _fail(job, f"too many restarts ({why})")
    _log(f"client {job['client_id']}: TikTok job {job['id']} restarts ({why})")
    _update(job["client_id"], job["id"], status="queued", restarts=restarts, next=0, upload_url=None,
            publish_id=None, attempts=0, retry_at=None)


def _retry_later(job: dict, error: Exception):
    attempts = int(job.get("attempts") or 0) + 1
    _log(f"client {job['client_id']}: TikTok job {job['id']} step failed "
         f"({type(error).__name__}: {str(error)[:200]}) — attempt {attempts}", severity="WARNING")
    if attempts >= MAX_ATTEMPTS:
        if job.get("status") == "uploading":
            # the session itself may be broken: a fresh init, then more attempts
            return _restart({**job, "attempts": 0}, f"{type(error).__name__} x{attempts}")
        return _fail(job, f"{type(error).__name__}: {str(error)[:300]}")
    wait = RETRY_AFTER_S[min(attempts - 1, len(RETRY_AFTER_S) - 1)]
    _update(job["client_id"], job["id"], attempts=attempts, retry_at=_iso(_now() + timedelta(seconds=wait)),
            last_error=f"{type(error).__name__}"[:80])


def _done(job: dict, detail: str):
    job = _update(job["client_id"], job["id"], status="done", outcome="done", finished_at=_iso(),
                  upload_url=None) or job
    try:
        from agents.client_agent import log_activity
        log_activity(job["client_id"], "tiktok_content_agent", "content_published",
                     {"drive_file_id": job["file_id"], "size_bytes": job.get("size"), "video": job.get("video"),
                      "owner_approved": bool(job.get("approved_by"))},
                     {"publish_id": job.get("publish_id"), "status": detail})
    except Exception:
        pass
    from core import agent_actions
    result = {"publish_id": job.get("publish_id", ""), "status": detail, "source_file": job.get("file_name", "")}
    if job.get("proposal_id"):
        agent_actions.finish_deferred(job["proposal_id"], "done", result=result)
    else:
        spec = agent_actions.ACTIONS["send_tiktok_to_inbox"]
        _say(job, spec["done_message"](result, job.get("lang") or "he"))


def _refused(job: dict, code: str, errors: list):
    job = _update(job["client_id"], job["id"], status="refused", outcome=code, finished_at=_iso(),
                  upload_url=None) or job
    _log(f"client {job['client_id']}: TikTok job {job['id']} refused ({code}): {'; '.join(map(str, errors))[:300]}")
    from core import agent_actions
    if job.get("proposal_id"):
        agent_actions.finish_deferred(job["proposal_id"], "failed", code=code)
    else:
        refusal = (agent_actions.ACTIONS["send_tiktok_to_inbox"].get("refusals") or {}).get(code)
        if refusal:
            _say(job, agent_actions._pick(refusal, job.get("lang") or "he"))


def _fail(job: dict, why: str):
    """Ends on OUR side: one admin alert; the client reads a calm line."""
    job = _update(job["client_id"], job["id"], status="failed", outcome="in_hand", finished_at=_iso(),
                  upload_url=None, last_error=why[:300]) or job
    _alert(f"client {job['client_id']}: TikTok upload of \"{job.get('file_name')}\" "
           f"({round((job.get('size') or 0) / 1048576)} MB, job {job['id']}) failed after retries: {why[:300]}")
    from core import agent_actions
    if job.get("proposal_id"):
        agent_actions.finish_deferred(job["proposal_id"], "failed", code="in_hand")
    else:
        _say(job, _pick(STILL_ON_IT, job.get("lang")).replace("{file}", job.get("file_name") or "—"))


def _say(job: dict, text: str):
    try:
        from core import agent_actions
        agent_actions._post_client_message(job["client_id"], job.get("persona") or "media", text)
    except Exception as e:
        _log(f"client {job.get('client_id')}: chat line not posted ({type(e).__name__})", severity="WARNING")


def admin_view(status: str = "open") -> list:
    out = []
    for jobs in _all_rows():
        for job in jobs:
            if status == "all" or (status == "open" and job.get("status") in OPEN) or job.get("status") == status:
                out.append({**_public(job), "client_name": _client_name(job["client_id"])})
    return sorted(out, key=lambda j: j.get("created_at") or "", reverse=True)
