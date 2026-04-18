"""
Flask API エンドポイント（ダッシュボード版）
- POST   /api/collect  : データ収集開始
- GET    /api/stream   : SSE で収集進捗を送信
- DELETE /api/collect  : 収集中断
- GET    /api/config   : 設定値を返す
"""

from __future__ import annotations

import json
import logging
import queue
import threading

from flask import Blueprint, Response, current_app, request, stream_with_context

from app.auth import get_access_token
from app.collect.onenote import fetch_onenote_texts
from app.collect.outlook import fetch_pending_emails
from app.collect.redmine import fetch_tickets, get_week_range
from app.generate.report import generate_dashboard

logger = logging.getLogger(__name__)
api = Blueprint("api", __name__)

_job: dict = {
    "running": False,
    "queue":   None,
    "cancel":  None,
    "result":  None,
    "error":   None,
}


# ── SSE ヘルパー ──
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ══════════════════════════════════════════
# /api/collect
# ══════════════════════════════════════════

@api.post("/api/collect")
def start_collect():
    if _job["running"]:
        return {"error": "収集が既に実行中です"}, 409

    body = request.get_json(silent=True) or {}
    week_offset = int(body.get("weekOffset", 0))

    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _job.update({
        "running": True,
        "queue":   q,
        "cancel":  cancel_event,
        "result":  None,
        "error":   None,
    })

    config = current_app.config["APP_CONFIG"]
    threading.Thread(
        target=_run_collect,
        args=(config, week_offset, q, cancel_event),
        daemon=True,
    ).start()

    return {"status": "started"}, 202


@api.delete("/api/collect")
def cancel_collect():
    if _job["running"] and _job["cancel"]:
        _job["cancel"].set()
        return {"status": "cancelled"}, 200
    return {"error": "実行中の収集がありません"}, 404


# ══════════════════════════════════════════
# /api/stream  (SSE)
# ══════════════════════════════════════════

@api.get("/api/stream")
def stream():
    def generate():
        q: queue.Queue = _job.get("queue")
        if q is None:
            yield _sse("error", {"message": "収集が開始されていません"})
            return
        while True:
            try:
                event = q.get(timeout=1.0)
                yield _sse(event["type"], event["data"])
                if event["type"] in ("done", "error", "cancelled"):
                    break
            except queue.Empty:
                yield ": ping\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════
# /api/config
# ══════════════════════════════════════════

@api.get("/api/config")
def get_config():
    config = current_app.config["APP_CONFIG"]
    return {
        "bossEmail":         config.boss_email,
        "bossEmailSubject":  config.boss_email_subject,
        "redmineProjects":   config.redmine_projects,
        "oneNoteNotebooks":  config.onenote_notebooks,
        "oneNoteSections":   config.onenote_sections,
    }


# ══════════════════════════════════════════
# バックグラウンド収集処理
# ══════════════════════════════════════════

def _run_collect(config, week_offset: int, q: queue.Queue, cancel: threading.Event):
    def emit(event_type: str, **kwargs):
        q.put({"type": event_type, "data": kwargs})

    try:
        # Step 1: 認証
        emit("step", label="Microsoft 認証中...", step=1, total=5)
        if cancel.is_set(): emit("cancelled", message="中断されました"); _job["running"] = False; return
        access_token = get_access_token(config)
        emit("step_done", step=1)

        # Step 2: Redmine
        emit("step", label="Redmine チケット取得中...", step=2, total=5)
        if cancel.is_set(): emit("cancelled", message="中断されました"); _job["running"] = False; return
        tickets = fetch_tickets(config, week_offset)
        emit("step_done", step=2)

        # Step 3: OneNote
        emit("step", label="OneNote 収集中...", step=3, total=5)
        if cancel.is_set(): emit("cancelled", message="中断されました"); _job["running"] = False; return
        onenote_pages = fetch_onenote_texts(access_token, config, week_offset)
        emit("step_done", step=3)

        # Step 4: メール
        emit("step", label="メール取得中...", step=4, total=5)
        if cancel.is_set(): emit("cancelled", message="中断されました"); _job["running"] = False; return
        emails = fetch_pending_emails(access_token, config)
        emit("step_done", step=4)

        # Step 5: AI タスク整理
        emit("step", label="AI でタスク整理中...", step=5, total=5)
        if cancel.is_set(): emit("cancelled", message="中断されました"); _job["running"] = False; return

        monday, sunday = get_week_range(week_offset)
        week_label = f"{monday.strftime('%Y/%m/%d')}〜{sunday.strftime('%m/%d')}"

        dashboard = generate_dashboard(config, tickets, onenote_pages, emails, week_label)
        _job["result"] = dashboard
        emit("step_done", step=5)

        emit("done", **dashboard)

    except Exception as e:
        logger.exception("収集処理でエラー")
        emit("error", message=str(e))
    finally:
        _job["running"] = False
