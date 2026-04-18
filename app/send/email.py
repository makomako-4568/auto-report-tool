"""
Microsoft Graph API でメールを送信する
"""

import logging

import requests

from app.config import Config

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def send_report_email(
    access_token: str,
    config: Config,
    report_text: str,
    reply_to_message_id: str | None = None,
) -> None:
    """
    レポートを上司にメール送信する。

    reply_to_message_id が指定された場合はスレッド返信、
    指定なしの場合は新規メールとして送信する。

    Args:
        access_token: Graph API アクセストークン
        config: 設定オブジェクト
        report_text: レポート本文（プレーンテキスト）
        reply_to_message_id: 返信元メッセージID（省略可）
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # HTML 本文に変換（改行を <br> に）
    html_body = report_text.replace("\n", "<br>\n")

    if reply_to_message_id:
        _send_reply(headers, reply_to_message_id, html_body, report_text)
    else:
        _send_new_email(headers, config, html_body, report_text)


def save_draft(
    access_token: str,
    config: Config,
    report_text: str,
) -> str:
    """
    レポートを Outlook の下書きとして保存し、メッセージIDを返す。
    GitHub Actions での自動実行時（レビューなし）に使用。
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    html_body = report_text.replace("\n", "<br>\n")
    payload = {
        "subject": f"Re: {config.boss_email_subject}",
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": config.boss_email}}],
    }

    resp = requests.post(
        f"{GRAPH_BASE}/me/messages",
        headers=headers,
        json=payload,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"下書き保存エラー: {e}\n{resp.text}") from e

    draft_id = resp.json().get("id", "")
    logger.info("下書き保存完了: message_id=%s", draft_id)
    return draft_id


def _send_reply(
    headers: dict,
    message_id: str,
    html_body: str,
    plain_body: str,
) -> None:
    """既存メッセージへの返信として送信する"""
    payload = {
        "message": {
            "body": {"contentType": "HTML", "content": html_body},
        },
        "comment": plain_body,
    }

    resp = requests.post(
        f"{GRAPH_BASE}/me/messages/{message_id}/reply",
        headers=headers,
        json=payload,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"メール返信エラー: {e}\n{resp.text}") from e

    logger.info("返信メール送信完了")


def _send_new_email(
    headers: dict,
    config: Config,
    html_body: str,
    plain_body: str,
) -> None:
    """新規メールとして送信する"""
    payload = {
        "message": {
            "subject": f"Re: {config.boss_email_subject}",
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": config.boss_email}}],
        },
        "saveToSentItems": True,
    }

    resp = requests.post(
        f"{GRAPH_BASE}/me/sendMail",
        headers=headers,
        json=payload,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"メール送信エラー: {e}\n{resp.text}") from e

    logger.info("新規メール送信完了")
