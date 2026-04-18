"""
Flask API エンドポイント
- POST /api/collect  : データ収集開始（バックグラウンドスレッド）
- GET  /api/stream   : SSE で収集進捗をクライアントに送信
- POST /api/send     : レポートメール送信
- GET  /api/config   : 現在の設定値を返す
"""

import json
import logging
import queue
import threading
import time
from datetime import date, timedelta

from flask import Blueprint, Response, current_app, request, stream_with_context

from app.auth import get_access_token
from app.collect.onenote import fetch_onenote_texts
from app.collect.outlook import find_boss_email
from app.collect.redmine import fetch_tickets, get_week_range
from app.generate.report import generate_report
from app.send.email import send_report_email

logger = logging.getLogger(__name__)
api = Blueprint("api", __name__)

# 収集ジョブの状態管理（シンプルなインメモリストア）
_job: dict = {
    "running": False,
    "queue": None,      # queue.Queue
    "cancel": None,     # threading.Event
    "result": None,     # 完了後のレポートテキスト
    "error": None,      # エラーメッセージ
    "message_id": None, # 返信元Outlookメッセージ ID
}


# ─────────────────────────────────────────
# SSE ヘルパー
# ─────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """SSE フォーマットに変換する"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─────────────────────────────────────────
# /api/collect
# ─────────────────────────────────────────

@api.post("/api/collect")
def start_collect():
    """データ収集をバックグラウンドスレッドで開始する"""
    if _job["running"]:
        return {"error": "収集が既に実行中です"}, 409

    body = request.get_json(silent=True) or {}
    week_offset = int(body.get("weekOffset", 0))

    # ジョブ状態をリセット
    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _job.update({
        "running": True,
        "queue": q,
        "cancel": cancel_event,
        "result": None,
        "error": None,
        "message_id": None,
    })

    config = current_app.config["APP_CONFIG"]
    thread = threading.Thread(
        target=_run_collect,
        args=(config, week_offset, q, cancel_event),
        daemon=True,
    )
    thread.start()
    return {"status": "started"}, 202


@api.delete("/api/collect")
def cancel_collect():
    """収集を中断する"""
    if _job["running"] and _job["cancel"]:
        _job["cancel"].set()
        return {"status": "cancelled"}, 200
    return {"error": "実行中の収集がありません"}, 404


# ─────────────────────────────────────────
# /api/stream  (SSE)
# ─────────────────────────────────────────

@api.get("/api/stream")
def stream():
    """SSE でジョブの進捗イベントを送信する"""
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
                # キープアライブ
                yield ": ping\n\n"

    resp = Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    return resp


# ─────────────────────────────────────────
# /api/send
# ─────────────────────────────────────────

@api.post("/api/send")
def send_email():
    """レビュー済みレポートをメール送信する"""
    body = request.get_json(silent=True) or {}
    report_text = body.get("report", "").strip()

    if not report_text:
        return {"error": "レポート本文が空です"}, 400

    config = current_app.config["APP_CONFIG"]
    errors = config.validate()
    if errors:
        return {"error": "設定エラー: " + ", ".join(errors)}, 500

    try:
        access_token = get_access_token(config)
        send_report_email(
            access_token=access_token,
            config=config,
            report_text=report_text,
            reply_to_message_id=_job.get("message_id"),
        )
    except Exception as e:
        logger.exception("メール送信失敗")
        return {"error": str(e)}, 500

    return {"status": "sent"}, 200


# ─────────────────────────────────────────
# /api/config
# ─────────────────────────────────────────

@api.get("/api/config")
def get_config():
    """現在の設定値をフロントエンドに返す（機密情報は除く）"""
    config = current_app.config["APP_CONFIG"]
    return {
        "bossEmail": config.boss_email,
        "bossEmailSubject": config.boss_email_subject,
        "redmineProjects": config.redmine_projects,
        "oneNoteNotebooks": config.onenote_notebooks,
        "oneNoteSections": config.onenote_sections,
    }


# ─────────────────────────────────────────
# バックグラウンド収集処理
# ─────────────────────────────────────────

def _run_collect(config, week_offset: int, q: queue.Queue, cancel: threading.Event):
    """バックグラウンドスレッドで収集〜生成を実行する"""

    def emit(event_type: str, **kwargs):
        q.put({"type": event_type, "data": kwargs})

    try:
        # ─── Step 1: 認証 ───
        emit("step", label="Microsoft 認証中...", step=1, total=4)
        if cancel.is_set():
            emit("cancelled", message="中断されました")
            _job["running"] = False
            return
        access_token = get_access_token(config)
        emit("step_done", step=1)

        # ─── Step 2: 上司メール検索 ───
        emit("step", label="上司のメールを検索中...", step=2, total=4)
        if cancel.is_set():
            emit("cancelled", message="中断されました")
            _job["running"] = False
            return
        boss_msg = find_boss_email(access_token, config)
        _job["message_id"] = boss_msg["id"] if boss_msg else None
        emit("step_done", step=2)

        # ─── Step 3: データ収集 ───
        emit("step", label="Redmine・OneNote からデータを収集中...", step=3, total=4)
        if cancel.is_set():
            emit("cancelled", message="中断されました")
            _job["running"] = False
            return

        monday, sunday = get_week_range(week_offset)
        week_label = f"{monday.strftime('%Y/%m/%d')}〜{sunday.strftime('%m/%d')}"

        tickets = fetch_tickets(config, week_offset)
        onenote_pages = fetch_onenote_texts(access_token, config, week_offset)
        emit("step_done", step=3)

        # ─── Step 4: AI レポート生成 ───
        emit("step", label="AI でレポートを生成中...", step=4, total=4)
        if cancel.is_set():
            emit("cancelled", message="中断されました")
            _job["running"] = False
            return

        report = generate_report(config, tickets, onenote_pages, week_label)
        _job["result"] = report
        emit("step_done", step=4)

        # 完了
        emit("done", report=report, weekLabel=week_label)

    except Exception as e:
        logger.exception("収集処理でエラーが発生")
        emit("error", message=str(e))

    finally:
        _job["running"] = False
